"""设置中心共用表单控件与分区卡片容器。"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.theme import _ui_font


def make_field_label(title: str, *, restart_required: bool = False, tooltip: str = "") -> QWidget:
    """构建带可选“需重启”徽章的表单左侧标签。"""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    label = QLabel(title, container)
    label.setFont(_ui_font(10))
    if tooltip:
        label.setToolTip(tooltip)
    layout.addWidget(label)

    if restart_required:
        badge = QLabel("需重启", container)
        badge.setObjectName("RestartBadge")
        badge.setToolTip("修改此项后需重启程序才能生效")
        layout.addWidget(badge)

    layout.addStretch(1)
    return container


class SettingsCard(QFrame):
    """圆角白色设置分组卡片。"""

    def __init__(self, title: str, description: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title_label = QLabel(title, self)
        title_label.setObjectName("SettingsCardTitle")
        title_label.setFont(_ui_font(11))
        root.addWidget(title_label)

        if description:
            desc_label = QLabel(description, self)
            desc_label.setObjectName("SettingsCardDesc")
            desc_label.setWordWrap(True)
            desc_label.setFont(_ui_font(9))
            root.addWidget(desc_label)

        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._form.setHorizontalSpacing(16)
        self._form.setVerticalSpacing(10)
        root.addLayout(self._form)

    def add_row(
        self,
        title: str,
        widget: QWidget,
        *,
        restart_required: bool = False,
        tooltip: str = "",
    ) -> None:
        if tooltip and not widget.toolTip():
            widget.setToolTip(tooltip)
        label_widget = make_field_label(title, restart_required=restart_required, tooltip=tooltip)
        self._form.addRow(label_widget, widget)

    def add_widget_row(self, widget: QWidget) -> None:
        self._form.addRow(widget)


class PasswordLineEdit(QWidget):
    """带显隐切换按钮的密钥输入框。"""

    def __init__(self, value: str = "", placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        self._edit = QLineEdit(self)
        self._edit.setFont(_ui_font(10))
        self._edit.setEchoMode(QLineEdit.EchoMode.Password)
        if placeholder:
            self._edit.setPlaceholderText(placeholder)
        self._edit.setText(value or "")

        self._toggle_btn = QPushButton("显示", self)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setFixedWidth(56)
        self._toggle_btn.toggled.connect(self._on_toggled)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._toggle_btn)

    def _on_toggled(self, checked: bool) -> None:
        self._edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self._toggle_btn.setText("隐藏" if checked else "显示")

    def text(self) -> str:
        return self._edit.text()

    def setText(self, value: str) -> None:
        self._edit.setText(value or "")


class TagListEditor(QLineEdit):
    """逗号/顿号分隔的列表编辑器，用于情绪白名单、敏感词或关键词配置。"""

    def __init__(self, items: list[str] | None = None, placeholder: str = "使用英文或中文逗号分隔", parent=None) -> None:
        super().__init__(parent)
        self.setFont(_ui_font(10))
        self.setPlaceholderText(placeholder)
        self.set_values(items or [])

    def set_values(self, items: list[str]) -> None:
        self.setText(", ".join(str(x).strip() for x in items if str(x).strip()))

    def values(self) -> list[str]:
        raw = self.text().replace("，", ",").replace("、", ",")
        out: list[str] = []
        seen: set[str] = set()
        for part in raw.split(","):
            item = part.strip()
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out


class PathPickerEdit(QWidget):
    """路径输入框 + 文件/目录浏览选择器。"""

    path_changed = Signal(str)

    def __init__(
        self,
        value: str = "",
        *,
        mode: str = "file",
        file_filter: str = "所有文件 (*.*)",
        placeholder: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._file_filter = file_filter

        self._edit = QLineEdit(self)
        self._edit.setFont(_ui_font(10))
        self._edit.setText(value or "")
        if placeholder:
            self._edit.setPlaceholderText(placeholder)
        self._edit.editingFinished.connect(lambda: self.path_changed.emit(self.text()))

        self._browse_btn = QPushButton("浏览…", self)
        self._browse_btn.setFixedWidth(68)
        self._browse_btn.clicked.connect(self._browse)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._browse_btn)

    def set_mode(self, mode: str, file_filter: str | None = None) -> None:
        self._mode = mode
        if file_filter is not None:
            self._file_filter = file_filter

    def _browse(self) -> None:
        current = self._edit.text().strip()
        if self._mode == "dir":
            chosen = QFileDialog.getExistingDirectory(self, "选择目录", current)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "选择文件", current, self._file_filter)
        if chosen:
            self._edit.setText(chosen)
            self.path_changed.emit(self.text())

    def text(self) -> str:
        return self._edit.text().strip()

    def setText(self, value: str) -> None:
        self._edit.setText(value or "")


class SliderSpinRow(QWidget):
    """滑块与浮点微调框双向联动控件。"""

    def __init__(
        self,
        value: float,
        minimum: float,
        maximum: float,
        *,
        step: float = 0.05,
        decimals: int = 2,
        scale_factor: int = 100,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._scale = scale_factor
        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        self._slider.setRange(int(round(minimum * scale_factor)), int(round(maximum * scale_factor)))

        self._spin = QDoubleSpinBox(self)
        self._spin.setFont(_ui_font(10))
        self._spin.setRange(minimum, maximum)
        self._spin.setSingleStep(step)
        self._spin.setDecimals(decimals)
        self._spin.setFixedWidth(86)

        self._slider.valueChanged.connect(self._on_slider_changed)
        self._spin.valueChanged.connect(self._on_spin_changed)
        self.set_value(value)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._spin)

    def _on_slider_changed(self, raw: int) -> None:
        self._spin.blockSignals(True)
        self._spin.setValue(raw / self._scale)
        self._spin.blockSignals(False)

    def _on_spin_changed(self, val: float) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(int(round(val * self._scale)))
        self._slider.blockSignals(False)

    def value(self) -> float:
        return round(float(self._spin.value()), self._spin.decimals())

    def set_value(self, val: float) -> None:
        self._spin.setValue(float(val))


class OptionalNumberEdit(QLineEdit):
    """可留空的整数或浮点输入框（空白表示 None）。"""

    def __init__(self, value: int | float | None, *, is_float: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._is_float = is_float
        self.setFont(_ui_font(10))
        self.setPlaceholderText("留空使用模型服务端默认值")
        if is_float:
            self.setValidator(QDoubleValidator(-1e9, 1e9, 4, self))
        else:
            self.setValidator(QIntValidator(-10**9, 10**9, self))
        if value is not None:
            self.setText(str(value))

    def value(self) -> int | float | None:
        raw = self.text().strip()
        if not raw:
            return None
        try:
            return float(raw) if self._is_float else int(raw)
        except ValueError:
            return None


def make_checkbox(text: str, checked: bool = False, *, tooltip: str = "") -> QCheckBox:
    """构建统一样式的设置复选框。"""
    box = QCheckBox(text)
    box.setFont(_ui_font(10))
    box.setChecked(bool(checked))
    if tooltip:
        box.setToolTip(tooltip)
    return box


def make_line_edit(text: str = "", *, placeholder: str = "") -> QLineEdit:
    """构建统一字体的单行文本输入框。"""
    edit = QLineEdit(text or "")
    edit.setFont(_ui_font(10))
    if placeholder:
        edit.setPlaceholderText(placeholder)
    return edit


def build_settings_page(page: QWidget, cards: list[QWidget] | tuple[QWidget, ...]) -> QVBoxLayout:
    """统一设置页容器对象名与卡片垂直布局。"""
    page.setObjectName("SettingsPageBody")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(2, 2, 8, 8)
    layout.setSpacing(12)
    for card in cards:
        layout.addWidget(card)
    layout.addStretch(1)
    return layout


def make_int_spin(value: int, minimum: int, maximum: int, *, step: int = 1) -> QSpinBox:
    box = QSpinBox()
    box.setFont(_ui_font(10))
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setValue(int(value))
    return box


def make_float_spin(
    value: float, minimum: float, maximum: float, *, step: float = 0.1, decimals: int = 2
) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setFont(_ui_font(10))
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setDecimals(decimals)
    box.setValue(float(value))
    return box


def make_combo(items: list[tuple[str, str]] | list[str], current: str | Enum, *, editable: bool = False) -> QComboBox:
    combo = QComboBox()
    combo.setFont(_ui_font(10))
    combo.setEditable(editable)
    for item in items:
        if isinstance(item, tuple):
            label, val = item
            combo.addItem(label, val)
        else:
            combo.addItem(item, item)

    if isinstance(current, Enum) or hasattr(current, "value"):
        current_val = str(current.value)
    else:
        current_val = "" if current is None else str(current)

    idx = combo.findData(current_val)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    elif editable:
        combo.setCurrentText(current_val)
    return combo


def combo_value(combo: QComboBox) -> str:
    if combo.isEditable():
        current_idx = combo.currentIndex()
        if current_idx >= 0 and combo.itemText(current_idx) == combo.currentText():
            data = combo.currentData()
            return str(data) if data is not None else combo.currentText().strip()
        return combo.currentText().strip()
    data = combo.currentData()
    return str(data if data is not None else combo.currentText())
