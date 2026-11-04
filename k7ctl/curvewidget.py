"""A small plot of an EQ profile's frequency response."""

from __future__ import annotations

import math

from . import eqcurve
from .eq import Profile
from .qt import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt

GRID_HZ = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
LABEL_HZ = {20: "20", 100: "100", 1000: "1k", 10000: "10k", 20000: "20k"}


class EqCurve(QtWidgets.QWidget):
    """Shows the curve with the preamp applied (what you hear) and dashed
    without it (the shape AutoEQ aims for)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points: list[tuple[float, float]] = []
        self.shape: list[tuple[float, float]] = []
        self.preamp = 0.0
        self.setMinimumHeight(150)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QtCore.QSize(480, 170)

    def set_profile(self, profile: Profile | None):
        if profile is None:
            self.points, self.shape, self.preamp = [], [], 0.0
        else:
            freqs = eqcurve.frequencies(240)
            self.points = eqcurve.response(profile, freqs)
            self.shape = eqcurve.response(profile, freqs, include_preamp=False)
            self.preamp = profile.safe_preamp
        self.update()

    def db_range(self) -> tuple[float, float]:
        vals = [db for _, db in self.points + self.shape] or [0.0]
        top = max(6.0, math.ceil(max(vals) / 3) * 3)
        bottom = min(-12.0, math.floor(min(vals) / 3) * 3)
        return bottom, top

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        text = pal.color(QtGui.QPalette.ColorRole.WindowText)
        grid = QtGui.QColor(text)
        grid.setAlpha(40)
        fm = p.fontMetrics()
        left, right, top, bottom = fm.horizontalAdvance("-12 dB") + 8, 8, 6, fm.height() + 6
        r = QtCore.QRectF(left, top, self.width() - left - right, self.height() - top - bottom)
        lo_db, hi_db = self.db_range()

        def x(hz):
            return r.left() + r.width() * math.log(hz / 20) / math.log(1000)

        def y(db):
            return r.top() + r.height() * (hi_db - db) / (hi_db - lo_db)

        p.setPen(QtGui.QPen(grid, 1))
        for hz in GRID_HZ:
            p.drawLine(QtCore.QPointF(x(hz), r.top()), QtCore.QPointF(x(hz), r.bottom()))
        step = 6 if hi_db - lo_db > 24 else 3
        db = math.ceil(lo_db / step) * step
        while db <= hi_db:
            p.drawLine(QtCore.QPointF(r.left(), y(db)), QtCore.QPointF(r.right(), y(db)))
            db += step

        p.setPen(text)
        for hz, label in LABEL_HZ.items():
            w = fm.horizontalAdvance(label)
            p.drawText(QtCore.QPointF(min(max(x(hz) - w / 2, r.left()), r.right() - w),
                                      r.bottom() + fm.ascent() + 3), label)
        db = math.ceil(lo_db / step) * step
        while db <= hi_db:
            label = f"{db:+.0f} dB" if db else "0 dB"
            p.drawText(QtCore.QPointF(left - fm.horizontalAdvance(label) - 4, y(db) + fm.ascent() / 2 - 1), label)
            db += step

        zero = QtGui.QColor(text)
        zero.setAlpha(110)
        p.setPen(QtGui.QPen(zero, 1))
        p.drawLine(QtCore.QPointF(r.left(), y(0)), QtCore.QPointF(r.right(), y(0)))

        if not self.points:
            p.setPen(text)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, tr("Pick or download a profile to see its curve"))
            p.end()
            return

        accent = pal.color(QtGui.QPalette.ColorRole.Highlight)
        if self.preamp:
            dim = QtGui.QColor(accent)
            dim.setAlpha(120)
            pen = QtGui.QPen(dim, 1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawPath(self._path(self.shape, x, y))
        p.setPen(QtGui.QPen(accent, 2.2))
        p.drawPath(self._path(self.points, x, y))
        p.end()

    @staticmethod
    def _path(points, x, y) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        for i, (hz, db) in enumerate(points):
            pt = QtCore.QPointF(x(hz), y(db))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        return path
