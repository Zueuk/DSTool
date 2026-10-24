"""Caption generator using Qt's QNetworkAccessManager for async HTTP."""

import json
import math

from PySide6.QtCore import QObject, QUrl, QBuffer, QIODevice, QByteArray, Signal
from PySide6.QtGui import Qt, QImage, QImageWriter
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from dstool.folder_config import DEFAULT_SERVER_URL

# Chat-completions endpoint appended to base URLs
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


def resolve_api_url(server_url: str) -> str:
    """Return the full chat-completions endpoint URL."""
    url_str = server_url.strip()
    if not url_str:
        url_str = DEFAULT_SERVER_URL
    url = QUrl(url_str)
    path = url.path()
    if not path or path == "/":
        url.setPath(CHAT_COMPLETIONS_PATH)
    return url.toString()


class CaptionRequest(QObject):
    """Async caption request via QNetworkAccessManager. Cancel via abort()."""

    # Signals
    progress = Signal(str, str)  # emitted with (text, level) tuple
    progress_html = Signal(str)  # emitted with raw HTML string
    finished_caption = Signal(str)  # emitted with final caption on success
    error = Signal(str)  # emitted with error message

    # Log levels for color coding
    LEVEL_DEBUG = "debug"
    LEVEL_INFO = "info"
    LEVEL_REASONING = "reasoning"
    LEVEL_CAPTION = "caption"
    LEVEL_ERROR = "error"

    @staticmethod
    def _classify_error(data: dict) -> tuple[str, str | None]:
        """Classify an error response from the server.

        Returns (category, message) where category is one of:
          - "image_unsupported" if server says images not supported
          - "server_error" for other explicit server errors
          - "none" if no recognizable error structure
        """
        if not isinstance(data, dict):
            return "none", None

        err = data.get("error")
        if not err or not isinstance(err, dict):
            return "none", None

        msg = (err.get("message") or err.get("msg") or "").strip()
        if not msg:
            return "none", None

        lower = msg.lower()
        # Typical pattern: "image input is not supported" / "images not supported"
        if "image" in lower and "not supported" in lower:
            return "image_unsupported", msg
        return "server_error", msg

    def __init__(
        self,
        server_url: str,
        timeout_seconds: int,
        guide_text: str,
        image_path: str,
        reasoning_enabled: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.server_url = server_url
        self.guide_text = guide_text
        self.image_path = image_path
        self._reasoning_enabled = reasoning_enabled
        self._timeout_seconds = timeout_seconds
        self._cancelled = False
        self._reply: QNetworkReply | None = None
        self._net_manager = QNetworkAccessManager(self)

        # Accumulators for streaming
        self._full_content = ""
        self._caption_content = ""
        self._chunk_count = 0

        # Error tracking to avoid duplicate error emissions
        self._error_emitted = False
        self._image_unsupported = False

    def cancel(self):
        self._cancelled = True
        if self._reply is not None:
            reply = self._reply  # Local reference
            try:
                # Send SSE cancel event to the server so it stops generating
                # Only write if the reply still supports writing (before abort)
                if reply.openMode() & QIODevice.WriteOnly:
                    cancel_event = (
                        b"event: cancel\r\n"
                        b"data: {\"stop_generation\": true}\r\n"
                        b"\r\n"
                    )
                    reply.write(cancel_event)
                    reply.flush()
            except Exception:
                pass
            try:
                reply.abort()
            except Exception:
                pass
            self._reply = None

    def start(self):
        self._full_content = ""
        self._caption_content = ""
        self._chunk_count = 0
        self._error_emitted = False
        self._image_unsupported = False

        self._log(self.LEVEL_INFO, f"Server URL: {self.server_url}")
        self._log(self.LEVEL_INFO, f"Captioning guide length: {len(self.guide_text)} chars, Reasoning: {self._reasoning_enabled}")

        try:
            img_b64 = self._encode_image(self.image_path)
        except Exception as e:
            self._log(self.LEVEL_ERROR, f"ERROR encoding image: {e}")
            self.error.emit(f"Failed to encode image: {e}")
            return

        self._send_request(self.server_url, self.guide_text, img_b64)

    def _encode_image(self, image_path: str, target_pixels: int = 1024 * 1024) -> str:
        """Load and resize image, return base64-encoded JPEG data using Qt."""
        img = QImage(image_path)
        if img.isNull():
            raise Exception(f"Failed to load image: {image_path}")

        self._log(self.LEVEL_DEBUG, f"Image {image_path} loaded: size={img.width()}x{img.height()}")

        original_width = img.width()
        original_height = img.height()
        original_pixels = original_width * original_height

        if original_pixels > target_pixels:
            scale = math.sqrt(target_pixels / original_pixels)
            new_width = max(1, int(original_width * scale))
            new_height = max(1, int(original_height * scale))
            # VLMs like dimensions to be multiples of 32
            new_width = (new_width // 32) * 32 or 32
            new_height = (new_height // 32) * 32 or 32
            img = img.scaled(new_width, new_height, Qt.KeepAspectRatio, Qt.FastTransformation)
            self._log(self.LEVEL_DEBUG, f"{original_width}x{original_height} exceeds {target_pixels} pixels -> resized to {new_width}x{new_height}")
        else:
            self._log(self.LEVEL_DEBUG, f"No resize needed ({original_pixels} <= {target_pixels} pixels)")

        # Save as JPEG into QBuffer and encode to base64
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        writer = QImageWriter(buffer, QByteArray(b"JPEG"))
        writer.setQuality(90)
        if not writer.write(img):
            buffer.close()
            raise Exception("Failed to write JPEG to buffer")
        encoded = bytes(buffer.data().toBase64()).decode("utf-8")
        buffer.close()

        self._log(self.LEVEL_DEBUG, f"JPEG size in memory: {len(buffer.data())} bytes, base64 length: {len(encoded)} chars")

        return encoded

    def _log(self, level: str, message: str):
        prefix = f"[{level.upper()}] "
        self.progress.emit(prefix + message + "\n", level)

    def _send_request(self, server_url: str, guide_text: str, img_b64: str):
        """Send image + prompt to the VLM server using QNetworkAccessManager.

        Uses streaming mode (SSE) with readyRead signal for real-time chunks.
        """
        user_content = [
            {
                "type": "text",
                "text": guide_text
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
            },
        ]

        payload = {
            "model": "",
            "messages": [{"role": "user", "content": user_content}],
            "max_tokens": 2048,
            "temperature": 0.7,
            "stream": True,
            "chat_template_kwargs": {
                "enable_thinking": self._reasoning_enabled,
            },
        }

        self._log(self.LEVEL_DEBUG, f"Payload: enable_thinking={self._reasoning_enabled}, max_tokens={payload['max_tokens']}, stream={payload['stream']}")

        try:
            self._send_request_streaming(server_url, payload, img_b64)
        except Exception as e:
            # Only sync errors in _send_request_streaming (e.g. invalid URL)
            self._log(self.LEVEL_ERROR, f"Streaming request setup failed: {e}")
            self.error.emit(f"Failed to send request: {e}")

    def _send_request_streaming(self, server_url: str, payload: dict, img_b64: str):
        """Send request with streaming (SSE) enabled via QNetworkAccessManager.

        Uses QNetworkReply.readyRead signal for real-time chunk processing.
        """
        timeout_ms = self._timeout_seconds * 1000
        self._log(self.LEVEL_DEBUG, f"POST to {server_url} (timeout={timeout_ms}ms, stream=True)")

        # Build JSON payload
        json_data = json.dumps(payload).encode("utf-8")

        # Create network request
        url = QUrl(resolve_api_url(server_url))
        request = QNetworkRequest(url)
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        request.setTransferTimeout(timeout_ms)

        # Send request
        self._reply = self._net_manager.post(request, json_data)

        # Connect signals for streaming
        self._reply.readyRead.connect(self._on_ready_read)
        self._reply.finished.connect(self._on_streaming_finished)
        self._reply.errorOccurred.connect(self._on_network_error)

    def _on_ready_read(self):
        """Process incoming data from the streaming response."""
        if self._cancelled or self._reply is None:
            return

        try:
            data = self._reply.readAll()
            if not data:
                return

            text = data.data().decode('utf-8', errors='replace')
            lines = text.split("\n")

            for line in lines:
                if self._cancelled:
                    self._log(self.LEVEL_INFO, "Cancelled by user during streaming")
                    return

                line = line.strip()
                if not line:
                    continue

                # SSE comment lines (": keep-alive" etc.) — ignore silently
                if line.startswith(":"):
                    continue

                if line.startswith("data:"):
                    data_str = line[len("data:"):].strip()

                    if data_str == "[DONE]":
                        self._log(self.LEVEL_DEBUG, "Received [DONE] from server")
                        self._finish_streaming()
                        return

                    # Parse SSE chunk
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError as e:
                        self._log(self.LEVEL_DEBUG, f"Failed to parse SSE chunk: {e}")
                        continue

                    if self._process_sse_chunk(chunk):
                        return
                else:
                    # Some servers answer an unsupported request with a bare JSON error body
                    # (no "data:" wrapper) plus an HTTP 5xx status.
                    if line.startswith("{"):
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            chunk = None
                        if isinstance(chunk, dict):
                            if self._process_sse_chunk(chunk):
                                return
                    self._log(self.LEVEL_DEBUG, f"Unexpected non-data SSE line: {line[:300]}")
                    continue

        except Exception as e:
            self._log(self.LEVEL_ERROR, f"Error reading response: {e}")

    def _process_sse_chunk(self, chunk: dict) -> bool:
        """Classify and process one SSE JSON chunk.

        Returns True if the response is to be terminated (a recognized
        server error was found), False to keep streaming.
        """
        if not isinstance(chunk, dict):
            return False

        # Check for explicit error object in chunk (e.g. image not supported)
        error_cat, error_msg = self._classify_error(chunk)
        if error_cat == "image_unsupported":
            if not self._error_emitted:
                self._error_emitted = True
                self._image_unsupported = True
                self._log(self.LEVEL_ERROR, f"Server does not support image inputs: {error_msg}")
                self.error.emit("Server does not support image inputs.")
            self._cleanup_reply()
            return True
        if error_cat == "server_error" and not self._error_emitted:
            self._error_emitted = True
            self._log(self.LEVEL_ERROR, f"Server error: {error_msg}")
            self.error.emit(f"Server error: {error_msg}")
            self._cleanup_reply()
            return True

        choices = chunk.get("choices")
        if not choices:
            # Only log if not an already-handled error chunk
            if not self._error_emitted:
                self._log(self.LEVEL_DEBUG, f"SSE chunk has no 'choices': {json.dumps(chunk, default=str)[:200]}")
            return False

        delta = choices[0].get("delta", {})
        if not delta:
            return False

        self._chunk_count += 1

        # Extract content
        if "content" in delta and delta["content"]:
            text_chunk = delta["content"]
            self._full_content += text_chunk
            self._caption_content += text_chunk
            self.progress.emit(text_chunk, self.LEVEL_CAPTION)

        # Extract reasoning content
        reasoning = (
            delta.get("reasoning", "")
            or delta.get("reasoning_content", "")
            or delta.get("thoughts", "")
            or ""
        )
        if reasoning:
            self._full_content += reasoning
            self.progress.emit(reasoning, self.LEVEL_REASONING)

        return False

    def _on_streaming_finished(self):
        """Handle QNetworkReply.finished signal for streaming mode.

        Used mainly for detecting that the reply ended (e.g. due to
        server closing the connection) without sending the SSE [DONE].
        """
        if self._cancelled:
            self._log(self.LEVEL_INFO, "Cancelled")
            self._cleanup_reply()
            return

        # If we already handled [DONE], nothing more to do
        if self._reply is None:
            return

        # If we already emitted a specific error (e.g. image_unsupported), don't add a second generic one
        if self._error_emitted:
            self._cleanup_reply()
            return

        # Connection closed without [DONE] and no caption -> treat as error
        caption = self._caption_content.strip()
        if not caption and not self._cancelled:
            self._log(
                self.LEVEL_ERROR,
                f"Streaming ended without caption "
                f"({self._chunk_count} chunks, {len(self._full_content)} chars)",
            )
            self.error.emit(
                "Generation ended without a caption "
                "(streaming connection closed unexpectedly)"
            )
            self._cleanup_reply()

    def _on_network_error(self, error):
        """Handle network errors (e.g., connection closed, timeout, abort)."""
        # OperationCanceledError is expected after explicit close() / abort()
        if error == QNetworkReply.NetworkError.OperationCanceledError:
            return

        # If already cancelled / cleaned up, nothing to do
        if self._cancelled:
            return

        # If we already emitted a specific error, don't add another
        if self._error_emitted:
            self._cleanup_reply()
            return

        # Mark before emitting: `finished` may be delivered (re-entrantly)
        # while the error dialog's modal event loop runs, before
        # _cleanup_reply() clears _reply — this prevents a second, generic
        # "streaming ended without caption" error from firing.
        self._error_emitted = True
        error_label = str(error)
        self._log(self.LEVEL_ERROR, f"Network error: {error_label}")

        # Emit a clear error message instead of raising (signal handlers
        # run asynchronously — exceptions would be silently swallowed).
        self.error.emit(f"Network error: {error_label}")
        self._cleanup_reply()

    def _finish_streaming(self):
        """Process final streaming content."""
        self._log(
            self.LEVEL_DEBUG,
            f"Streaming complete: {self._chunk_count} chunks, {len(self._full_content)} chars total, {len(self._caption_content)} chars caption"
        )

        # Determine final content (caption only, excluding reasoning)
        content = self._caption_content.strip()

        if self._cancelled:
            self._log(self.LEVEL_INFO, "Cancelled")
            self._cleanup_reply()
            return

        # If we already emitted a specific error (e.g. image_unsupported), don't add another
        if self._error_emitted:
            self._cleanup_reply()
            return

        if content:
            self.finished_caption.emit(content)
        else:
            # Streaming finished but no caption content: report as error so
            # the UI can reset button state and alert the user.
            self._log(
                self.LEVEL_ERROR,
                "Streaming ended with no caption content",
            )
            self.error.emit(
                "Generation finished but no caption was produced "
                "(possible timeout or empty response)"
            )

        self._cleanup_reply()

    def _cleanup_reply(self):
        """Clean up the network reply."""
        if self._reply is not None:
            try:
                self._reply.close()
                self._reply.readyRead.disconnect()
            except Exception:
                pass
            self._reply = None
