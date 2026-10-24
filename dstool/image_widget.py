import math
import os

from PySide6.QtCore import (
    Qt, QPointF, QRect, QRectF, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QBitmap, QBrush, QImage, QPainter, QPen, QPixmap, QPainterPath,
    QRegion, QTransform, QUndoStack, QUndoCommand,
)
from PySide6.QtWidgets import QWidget


MAX_BRUSH_SIZE = 256
PIXELATE_AREA = 100
ANTS_TIMER_MSEC = 100  # 10 fps


def create_checkerboard(width:int, height:int, cell_size=16):
    """Photoshop-style checkerboard placeholder."""
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(120, 120, 120))
    painter = QPainter(pixmap)
    dark_brush = QBrush(QColor(80, 80, 80))
    for y in range(0, height, cell_size * 2):
        for x in range(0, width, cell_size * 2):
            painter.fillRect(x + cell_size, y, cell_size, cell_size, dark_brush)
            painter.fillRect(x, y + cell_size, cell_size, cell_size, dark_brush)
    painter.end()
    return pixmap

def create_pixelated_thumbnail(image: QImage, max_area: int = PIXELATE_AREA) -> QImage:
    """Downsize image to max_area pixels total, preserving aspect ratio."""
    orig_w, orig_h = image.width(), image.height()
    if orig_w <= 0 or orig_h <= 0:
        return image.copy()

    orig_area = orig_w * orig_h
    scale = (max_area / orig_area) ** 0.5
    new_w = max(1, int(orig_w * scale))
    new_h = max(1, int(orig_h * scale))

    return image.scaled(new_w, new_h, Qt.IgnoreAspectRatio, Qt.FastTransformation)

def is_all_white(image: QImage) -> bool:
    """C-speed scan: True if all pixels are 255, only valid for QImage.Format_Grayscale8."""
    w, h, bpl = image.width(), image.height(), image.bytesPerLine()
    mv = image.constBits()
    if bpl == w:
        return bytes(mv) == b'\xff' * (w * h)
    row = b'\xff' * w
    for y in range(h):
        if bytes(mv[y * bpl:y * bpl + w]) != row:
            return False
    return True


class MaskStrokeCommand(QUndoCommand):
    """Undo command for mask brush strokes, stores only changed region.

    old_crop=None: region was all-255 (empty mask) - undo fills it with 255.
    new_crop=None: region became all-255 (cleared) - redo fills it with 255.
    """

    def __init__(self, mask_image: QImage, rect: QRect, old_crop: QImage | None, new_crop: QImage | None = None, parent=None):
        super().__init__(parent)
        self._mask_image = mask_image
        self._rect = rect
        self._old_crop = old_crop
        self._new_crop = new_crop
        self.setText("Stroke")

    def undo(self):
        painter = QPainter(self._mask_image)
        if self._old_crop is None:
            painter.fillRect(self._rect, Qt.white)
        else:
            painter.drawImage(self._rect.topLeft(), self._old_crop)
        painter.end()

    def redo(self):
        painter = QPainter(self._mask_image)
        if self._new_crop is None:
            painter.fillRect(self._rect, Qt.white)
        else:
            painter.drawImage(self._rect.topLeft(), self._new_crop)
        painter.end()

    def mergeWith(self, source: QUndoCommand) -> bool:
        if not isinstance(source, MaskStrokeCommand):
            return False
        if self._rect != source._rect:
            return False
        # Merged state spans this command through source:
        # keep the earliest old state (self), take the latest new state (source)
        self._new_crop = source._new_crop
        self.setText(source.text())
        return True


class MaskInvertCommand(QUndoCommand):
    """Invert mask — self-inverse, no crop data needed."""

    def __init__(self, mask_image: QImage, parent=None):
        super().__init__(parent)
        self._mask_image = mask_image
        self.setText("Invert")

    def undo(self):
        self._mask_image.invertPixels()

    def redo(self):
        self._mask_image.invertPixels()


class ImageWidget(QWidget):
    maskModified = Signal()
    brushRadiusChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(100, 100)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        self.original_image: QImage = QImage()
        self.mask_image: QImage = QImage()

        self.scale = 1.0
        self.img_rect = QRectF()
        self.cached_image: QPixmap | None = None              # original image for outline mode
        self.cached_thumbnail: QPixmap | None = None          # pixelated thumbnail for hide-image mode
        self.cached_mask: QPixmap | None = None               # lightened mask for Multiply overlay

        self._outline_mode = False
        self._outline_dirty = True
        self._outline_path = QPainterPath()
        self._ants_offset = 0
        self._ants_timer = QTimer(self)
        self._ants_timer.timeout.connect(self._advance_ants)
        self._hide_image_mode = False
        self._image_missing = False
        # Create a Photoshop-style checkerboard pattern for missing images
        checker_pixmap = create_checkerboard(32, 32, cell_size=16)
        self._missing_image_brush = QBrush(checker_pixmap)

        self.drawing = False
        self.last_pos = QPointF()
        self.brush_radius = 32
        self.brush_value = 1.0
        self.current_mouse_button = None
        self.hover_pos = None

        self._stroke_original_mask: QImage | None = None
        self._stroke_rect = QRect()

        # QUndoStack for mask editing
        self.undo_stack = QUndoStack(self)

    def load_images(self, image: QImage | None, mask: QImage):
        if image is not None:
            self.original_image = image.copy()
        else:
            self.original_image = QImage()
        self.mask_image = mask.copy()
        self.undo_stack.clear()
        self._stroke_original_mask = None
        self._stroke_rect = QRect()
        self.cached_image = None
        self.cached_thumbnail = None
        self.cached_mask = None
        self._outline_dirty = True
        self.update()

    def set_outline_mode(self, enabled: bool):
        if self._outline_mode == enabled:
            return
        self._outline_mode = enabled
        if enabled:
            self._outline_dirty = True
            self._ants_timer.start(ANTS_TIMER_MSEC)
        else:
            self._ants_timer.stop()
        self.update()

    def set_hide_image_mode(self, enabled: bool):
        if self._hide_image_mode == enabled:
            return
        self._hide_image_mode = enabled
        self.cached_thumbnail = None
        self.cached_mask = None
        self.update()

    def set_image_missing(self, missing: bool):
        """Set whether the original image file is missing from disk.
        When True, displays pixelated thumbnail with mask overlay (same as hide image mode).
        """
        if self._image_missing == missing:
            return
        self._image_missing = missing
        self.cached_thumbnail = None
        self.cached_mask = None
        self.update()

    def get_mask(self) -> QImage:
        return self.mask_image

    def is_mask_dirty(self) -> bool:
        """Return True if current mask differs from the saved state.

        Uses QUndoStack's built-in clean/dirty tracking: the mask is dirty
        when the current stack position differs from the last saved position.
        """
        return not self.undo_stack.isClean()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)

        # Rebuild caches if needed
        if self.cached_image is None:
            self._rebuild_display()
        if self._hide_image_mode or self._image_missing:
            if self.cached_thumbnail is None:
                self._rebuild_thumbnail()
        if self.cached_mask is None:
            self._rebuild_cached_mask()

        # Step 1: Draw background (depends on mode)
        if self._image_missing:
            # Checkerboard pattern
            painter.fillRect(self.img_rect, self._missing_image_brush)
        elif self._hide_image_mode:
            # Pixelated thumbnail (scaled to img_rect for proper mask alignment)
            if self.cached_thumbnail:
                painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
                painter.drawPixmap(self.img_rect, self.cached_thumbnail, self.cached_thumbnail.rect())
        else:
            # Original image background
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            if self.cached_image:
                painter.drawPixmap(self.img_rect.topLeft(), self.cached_image)

        # Step 2: Mask overlay (single Multiply pass)
        if not self._outline_mode:
            # Normal mask overlay - cached_mask is pre-composed with lightened mask
            if self.cached_mask is not None:
                painter.save()
                painter.setCompositionMode(QPainter.CompositionMode_Multiply)
                painter.drawPixmap(self.img_rect.topLeft(), self.cached_mask)
                painter.restore()
        else:
            # Marching ants
            if self._outline_dirty:
                self._rebuild_outline_path()
            if not self._outline_path.isEmpty():
                painter.save()
                painter.setCompositionMode(QPainter.RasterOp_SourceXorDestination)
                pen = QPen(Qt.white, 0, Qt.CustomDashLine)
                pen.setDashPattern([4, 4])
                pen.setDashOffset(self._ants_offset)
                painter.setPen(pen)
                painter.drawPath(self._outline_path)
                painter.restore()

        # Brush preview
        if self.hover_pos is not None and self.img_rect.isValid():
            r = self.brush_radius
            painter.save()
            pen = QPen(QColor(255, 255, 255, 180))
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(QBrush())
            painter.setCompositionMode(QPainter.RasterOp_SourceXorDestination)
            painter.drawEllipse(self.hover_pos, r, r)
            painter.restore()

    def resizeEvent(self, event):
        # Only cached_image/cached_mask depend on display dimensions.
        # cached_thumbnail is fixed-size and doesn't need rebuild on resize.
        self.cached_image = None
        self.cached_mask = None
        self._outline_dirty = True
        super().resizeEvent(event)

    def _compute_display_dims(self):
        """Compute display dimensions and scale from current widget size.
        Returns (scale, disp_w, disp_h, off_x, off_y, img_w, img_h) or None.
        """
        if self.original_image.isNull() or self.mask_image.isNull():
            return None
        w, h = self.width(), self.height()
        if w < 1 or h < 1:
            return None
        img_w, img_h = self.original_image.width(), self.original_image.height()
        scale = min(w / img_w, h / img_h)
        disp_w, disp_h = int(img_w * scale), int(img_h * scale)
        if disp_w < 1 or disp_h < 1:
            return None
        off_x, off_y = (w - disp_w) / 2, (h - disp_h) / 2
        return scale, disp_w, disp_h, off_x, off_y, img_w, img_h

    def _rebuild_thumbnail(self):
        """Build pixelated thumbnail for hide-image mode."""
        if self.original_image.isNull() or self.mask_image.isNull():
            self.cached_thumbnail = None
            return

        info = self._compute_display_dims()
        if info is None:
            self.cached_thumbnail = None
            return

        scale, disp_w, disp_h, off_x, off_y, img_w, img_h = info
        self.scale = scale
        self.img_rect = QRectF(off_x, off_y, disp_w, disp_h)

        # Create a small pixelated thumbnail (~100 pixels total area) and store directly
        self.cached_thumbnail = QPixmap.fromImage(create_pixelated_thumbnail(self.original_image))

    def _rebuild_cached_mask(self):
        """Pre-compose mask onto white background for overlay in paintEvent.

        Draws the scaled mask onto white using SourceOver with opacity=0.8:
        - mask=0 → 0.8*0 + 0.2*white = light gray (~51) → darkens via Multiply
        - mask=255 → 0.8*white + 0.2*white = white (255) → no darkening via Multiply
        Then uses Multiply blend mode in paintEvent to composite over the image.
        Works even when original image is missing (uses mask_image size directly).
        """
        if self.mask_image.isNull():
            self.cached_mask = None
            return

        # If original_image is available, use normal display dims; otherwise use mask size
        if not self.original_image.isNull():
            info = self._compute_display_dims()
            if info is None:
                self.cached_mask = None
                return
            scale, disp_w, disp_h, off_x, off_y, img_w, img_h = info
        else:
            # No original image: use mask size as display size (no scaling)
            mw, mh = self.mask_image.width(), self.mask_image.height()
            w, h = self.width(), self.height()
            if w < 1 or h < 1 or mw < 1 or mh < 1:
                self.cached_mask = None
                return
            scale = min(w / mw, h / mh)
            disp_w, disp_h = int(mw * scale), int(mh * scale)
            off_x, off_y = (w - disp_w) / 2, (h - disp_h) / 2
            self.scale = scale

        self.img_rect = QRectF(off_x, off_y, disp_w, disp_h)

        # Create white background for the darkened overlay
        white_bg = QImage(disp_w, disp_h, QImage.Format_ARGB32)
        white_bg.fill(Qt.white)

        # Convert grayscale mask to ARGB32 for SourceOver composition
        mask_argb = self.mask_image.convertToFormat(QImage.Format_ARGB32)

        # Draw the scaled mask onto white using SourceOver with opacity=0.8.
        # QTransform with integer scale factors gives nearest-neighbor scaling.
        painter = QPainter(white_bg)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        transform = QTransform().scale(disp_w / self.mask_image.width(), disp_h / self.mask_image.height())
        painter.setTransform(transform)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setOpacity(0.8)
        painter.drawImage(0, 0, mask_argb)
        painter.end()

        self.cached_mask = QPixmap.fromImage(white_bg)

    def _rebuild_display(self):
        if self.original_image.isNull() or self.mask_image.isNull():
            return
        w, h = self.width(), self.height()
        if w < 1 or h < 1:
            return
        img_w, img_h = self.original_image.width(), self.original_image.height()
        self.scale = min(w / img_w, h / img_h)
        disp_w, disp_h = int(img_w * self.scale), int(img_h * self.scale)
        off_x, off_y = (w - disp_w) / 2, (h - disp_h) / 2
        self.img_rect = QRectF(off_x, off_y, disp_w, disp_h)

        scaled_img = self.original_image.scaled(disp_w, disp_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.cached_image = QPixmap.fromImage(scaled_img)

    def _rebuild_outline_path(self):
        self._outline_path = QPainterPath()
        if self.mask_image.isNull():
            self._outline_dirty = False
            return

        # Compute img_rect from mask size when no original image exists
        if self.original_image.isNull():
            mw, mh = self.mask_image.width(), self.mask_image.height()
            w, h = self.width(), self.height()
            if w < 1 or h < 1 or mw < 1 or mh < 1:
                self._outline_dirty = False
                return
            self.scale = min(w / mw, h / mh)
            disp_w, disp_h = int(mw * self.scale), int(mh * self.scale)
            off_x, off_y = (w - disp_w) / 2, (h - disp_h) / 2
            self.img_rect = QRectF(off_x, off_y, disp_w, disp_h)
        else:
            if not self.img_rect.isValid():
                self._outline_dirty = False
                return
            disp_w = int(self.img_rect.width())
            disp_h = int(self.img_rect.height())

        if disp_w < 1 or disp_h < 1:
            self._outline_dirty = False
            return

        # Directly use mask_image as QImage for bitmap conversion
        bitmap = QBitmap.fromImage(self.mask_image, Qt.ThresholdDither | Qt.AutoColor)
        region = QRegion(bitmap)
        path = QPainterPath()
        path.addRegion(region)

        simplified_path = path.simplified()

        # Resize and translate to image position within the widget
        mw, mh = self.mask_image.width(), self.mask_image.height()
        scale_x = disp_w / mw if mw > 0 else 1.0
        scale_y = disp_h / mh if mh > 0 else 1.0
        transform = QTransform().translate(self.img_rect.x(), self.img_rect.y()).scale(scale_x, scale_y)
        outline = transform.map(simplified_path)
        self._outline_path = outline
        self._outline_dirty = False

    def _advance_ants(self):
        self._ants_offset = (self._ants_offset + 1) % 8
        self.update()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _image_coords(self, pos: QPointF):
        """Convert widget coordinates to image (original_image) coordinates."""
        if not self.img_rect.isValid():
            return None
        ix = (pos.x() - self.img_rect.x()) / self.scale
        iy = (pos.y() - self.img_rect.y()) / self.scale
        return QPointF(ix, iy)

    def _img_to_mask_transform(self):
        """Return QTransform mapping image coords to mask coords, or identity if same size."""
        if self.original_image.isNull() or self.mask_image.isNull():
            return QTransform()
        iw, ih = self.original_image.width(), self.original_image.height()
        mw, mh = self.mask_image.width(), self.mask_image.height()
        if iw == mw and ih == mh:
            return QTransform()
        return QTransform().scale(mw / iw, mh / ih)

    def _mask_coords_from_image(self, img_pt: QPointF) -> QPointF:
        """Map a point in image space to mask space (handles size mismatch)."""
        if self.original_image.isNull() or self.mask_image.isNull():
            return img_pt
        iw, ih = self.original_image.width(), self.original_image.height()
        mw, mh = self.mask_image.width(), self.mask_image.height()
        if iw == mw and ih == mh:
            return img_pt
        return QPointF(img_pt.x() * mw / iw, img_pt.y() * mh / ih)

    def _brush_radii_in_mask(self, r_display: float) -> tuple[float, float]:
        """Convert brush radius in display pixels to mask-space radii (rx, ry),
        accounting for different image/mask aspect ratios.
        """
        if self.mask_image.isNull():
            return float(r_display), float(r_display)
        if self.original_image.isNull():
            r_mask = r_display / self.scale if self.scale > 0 else r_display
            return float(r_mask), float(r_mask)
        # Convert display pixels to image-space pixels
        r_image = r_display / self.scale if self.scale > 0 else r_display
        iw, ih = self.original_image.width(), self.original_image.height()
        mw, mh = self.mask_image.width(), self.mask_image.height()
        if iw == mw and ih == mh:
            return float(r_image), float(r_image)
        rx = r_image * (mw / iw)
        ry = r_image * (mh / ih)
        return rx, ry

    def _get_brush_rect(self, center: QPointF) -> QRect:
        """Return the bounding box of the brush in mask-space as a QRect."""
        rx, ry = self._brush_radii_in_mask(self.brush_radius)
        w, h = self.mask_image.width(), self.mask_image.height()
        x0 = max(math.floor(center.x() - rx), 0)
        y0 = max(math.floor(center.y() - ry), 0)
        x1 = min( math.ceil(center.x() + rx), w)
        y1 = min( math.ceil(center.y() + ry), h)
        return QRect(x0, y0, x1 - x0, y1 - y0)

    def _invalidate_mask_display(self):
        self.cached_mask = None
        self._outline_dirty = True
        self.update()

    def _draw_ellipse_on_mask(self, center: QPointF, rx: float, ry: float, fill: int):
        """Draw an ellipse on the mask_image using QPainter."""
        painter = QPainter(self.mask_image)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        if fill == 0:
            painter.setBrush(QColor(0, 0, 0))
        else:
            painter.setBrush(QColor(fill, fill, fill))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, rx, ry)
        painter.end()

    def _compare_mask_region(self, img1: QImage, img2: QImage, rect: QRect) -> bool:
        """Return True if regions are different."""
        r = img1.rect().intersected(rect)
        if not r.isValid():
            return False

        bpp = img1.depth() // 8 if img1.depth() > 0 else 1
        row_bytes = r.width() * bpp

        bpl1 = img1.bytesPerLine()
        bpl2 = img2.bytesPerLine()

        start1 = r.y() * bpl1 + r.x() * bpp
        start2 = r.y() * bpl2 + r.x() * bpp

        mv1 = img1.constBits()
        mv2 = img2.constBits()

        for y in range(r.height()):
            offset1 = start1 + y * bpl1
            offset2 = start2 + y * bpl2
            if mv1[offset1:offset1 + row_bytes] != mv2[offset2:offset2 + row_bytes]:
                return True
        return False


    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        pos = event.position()
        img_pt = self._image_coords(pos)
        if img_pt is None or event.button() not in (Qt.LeftButton, Qt.RightButton):
            return

        self.grabMouse()
        self.drawing = True
        self.last_pos = img_pt
        self.current_mouse_button = event.button()

        fill = int(self.brush_value * 255) if event.button() == Qt.LeftButton else 0
        r = self.brush_radius

        # Convert image-space point to mask-space for painting
        mask_pt = self._mask_coords_from_image(img_pt)
        rx, ry = self._brush_radii_in_mask(r)

        self._stroke_original_mask = self.mask_image.copy()

        # If left-clicking on an all-255 (empty) mask, zero-fill it first;
        # the changed region is the whole mask, so track it as such
        if event.button() == Qt.LeftButton and self._is_mask_empty():
            self.mask_image.fill(0)
            brush_rect = self.mask_image.rect()
        else:
            brush_rect = self._get_brush_rect(mask_pt)

        self._draw_ellipse_on_mask(mask_pt, rx, ry, fill)

        # Check if the first point actually changed anything
        if self._compare_mask_region(self._stroke_original_mask, self.mask_image, brush_rect):
            self._stroke_rect = brush_rect
            self._invalidate_mask_display()
            self.maskModified.emit()
        else:
            self._stroke_rect = QRect()

    def mouseMoveEvent(self, event):
        self.hover_pos = event.position()
        img_pt = self._image_coords(self.hover_pos)
        if img_pt is None:
            return

        if self.drawing:
            fill = int(self.brush_value * 255) if self.current_mouse_button == Qt.LeftButton else 0
            r = self.brush_radius
            if self.scale > 0:
                r = r / self.scale

            # Skip zero-length strokes (mouse hasn't moved in image space)
            dx = img_pt.x() - self.last_pos.x()
            dy = img_pt.y() - self.last_pos.y()
            if abs(dx) < 0.01 and abs(dy) < 0.01:
                self.last_pos = img_pt
                self.update()
                return

            # Build a continuous QPainterPath for the stroke in IMAGE coordinates
            path = QPainterPath()
            path.moveTo(self.last_pos)
            path.lineTo(img_pt)

            # Single image to mask scale transform applied to painter
            painter = QPainter(self.mask_image)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            t = self._img_to_mask_transform()
            painter.setTransform(t, combine=True)
            pen = QPen(QColor(fill, fill, fill), 2 * r, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)
            painter.end()

            # Track the bounding region for undo/redo: work in image-space and transform at the end
            # Build accumulated rect in image-space, expanded by brush radius
            stroke_rect = QRectF(self.last_pos, img_pt).normalized().adjusted(-r, -r, r, r)
            # Union with new stroke rect (both now in mask-space)
            assert self._stroke_rect is not None
            self._stroke_rect = self._stroke_rect.united(t.mapRect(stroke_rect).toAlignedRect())

            self.last_pos = img_pt

            self._stroke_rect = self._stroke_rect.intersected(self.mask_image.rect())
            if self._compare_mask_region(self._stroke_original_mask, self.mask_image, self._stroke_rect):
                self._invalidate_mask_display()
                self.maskModified.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drawing:
            self.drawing = False
            self.current_mouse_button = None
            if QWidget.mouseGrabber() is self:
                QTimer.singleShot(0, self.releaseMouse)

        if self._stroke_original_mask and self._stroke_rect:
            if self._compare_mask_region(self._stroke_original_mask, self.mask_image, self._stroke_rect):
                # Stroke on an empty mask zero-filled the whole mask: the old
                # state was uniform 255, stored as None (undo = fill with 255)
                old_crop = None if is_all_white(self._stroke_original_mask) else self._stroke_original_mask.copy(self._stroke_rect)
                new_crop = self.mask_image.copy(self._stroke_rect)
                self.undo_stack.push(MaskStrokeCommand(self.mask_image, self._stroke_rect, old_crop, new_crop))
                self._invalidate_mask_display()
                self.maskModified.emit()
        self._stroke_original_mask = None
        self._stroke_rect = QRect()

    def leaveEvent(self, event):
        self.hover_pos = None
        self.update()

    def decrease_brush_size(self):
        size = self.brush_radius
        delta = 1 if size < 16 else 2 if size < 32 else 4 if size < 64 else 8
        self.brush_radius = max(1, size - delta)
        self.brushRadiusChanged.emit(self.brush_radius)

    def increase_brush_size(self):
        size = self.brush_radius
        delta = 1 if size < 16 else 2 if size < 32 else 4 if size < 64 else 8
        self.brush_radius = min(MAX_BRUSH_SIZE, size + delta)
        self.brushRadiusChanged.emit(self.brush_radius)

        # When drawing and brush is increasing, draw ellipse with new size
        if self.drawing and self.hover_pos is not None:
            img_pt = self._image_coords(self.hover_pos)
            mask_pt = self._mask_coords_from_image(img_pt)
            rx, ry = self._brush_radii_in_mask(self.brush_radius)
            fill = int(self.brush_value * 255) if self.current_mouse_button == Qt.LeftButton else 0
            self._draw_ellipse_on_mask(mask_pt, rx, ry, fill)
            # Merge stroke rect with new brush size rect for proper undo tracking
            brush_rect = self._get_brush_rect(mask_pt)
            assert self._stroke_rect is not None
            self._stroke_rect = self._stroke_rect.united(brush_rect)
            self._stroke_rect = self._stroke_rect.intersected(self.mask_image.rect())
            # Only mark dirty if the brush expansion actually changed pixels
            if self._compare_mask_region(self._stroke_original_mask, self.mask_image, self._stroke_rect):
                self._invalidate_mask_display()
                self.maskModified.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta < 0:
            self.decrease_brush_size()
        elif delta > 0:
            self.increase_brush_size()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.drawing:
            if self._stroke_original_mask:
                self.mask_image = self._stroke_original_mask
                self._invalidate_mask_display()
                self.maskModified.emit()
            self.drawing = False
            self.current_mouse_button = None
            self._stroke_original_mask = None
            self._stroke_rect = QRect()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------
    def undo(self):
        if self.drawing:
            return
        self.undo_stack.undo()
        self._invalidate_mask_display()
        self.maskModified.emit()

    def redo(self):
        if self.drawing:
            return
        self.undo_stack.redo()
        self._invalidate_mask_display()
        self.maskModified.emit()

    # ------------------------------------------------------------------
    # Mask operations
    # ------------------------------------------------------------------
    def _is_mask_empty(self) -> bool:
        return is_all_white(self.mask_image)

    def clear_mask(self):
        if self.mask_image.isNull() or self._is_mask_empty():
            return
        full_rect = self.mask_image.rect()
        cmd = MaskStrokeCommand(self.mask_image, full_rect, self.mask_image.copy(full_rect), None)
        self.undo_stack.push(cmd)
        self.mask_image.fill(255)
        self._invalidate_mask_display()
        self.maskModified.emit()

    def invert_mask(self):
        if self.mask_image.isNull():
            return
        cmd = MaskInvertCommand(self.mask_image)
        self.undo_stack.push(cmd)
        self._invalidate_mask_display()
        self.maskModified.emit()

    def save_mask(self, path: str):
        if self.mask_image.isNull():
            return
        if self._is_mask_empty():
            if os.path.exists(path):
                os.remove(path)
        else:
            self.mask_image.save(path, "PNG")  # type: ignore[arg-type]
