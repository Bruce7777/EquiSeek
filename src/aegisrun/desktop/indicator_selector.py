from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QToolButton


class MultiIndicatorSelector(QToolButton):
    """Compact 1-3 item selector with visible limits and keyboard-accessible actions."""

    MODES = ("MA", "BOLL", "MACD", "KDJ", "RSI", "ATR", "WR")
    MAX_SELECTED = 3
    selection_changed = Signal(object)

    def __init__(self, selected: Sequence[str] = ("MA", "MACD", "WR")) -> None:
        super().__init__()
        self.setObjectName("indicatorSelector")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip("选择 1–3 个指标；MA/BOLL 叠加主图，其他指标分层显示")
        menu = QMenu(self)
        menu.setAccessibleName("技术指标多选菜单")
        self.setMenu(menu)
        self._actions: dict[str, QAction] = {}
        self._updating = False
        for mode in self.MODES:
            action = QAction(mode, self)
            action.setCheckable(True)
            action.setToolTip(f"显示 {mode} 指标")
            action.toggled.connect(
                lambda checked, selected_mode=mode: self._action_toggled(selected_mode, checked)
            )
            menu.addAction(action)
            self._actions[mode] = action
        self.set_selected(selected)

    @property
    def selected_indicators(self) -> tuple[str, ...]:
        return tuple(mode for mode in self.MODES if self._actions[mode].isChecked())

    def action_for(self, mode: str) -> QAction:
        try:
            return self._actions[mode]
        except KeyError as error:
            raise ValueError(f"未知技术指标：{mode}") from error

    def set_selected(self, selected: Sequence[str], *, emit: bool = False) -> None:
        values = tuple(selected)
        if not 1 <= len(values) <= self.MAX_SELECTED:
            raise ValueError("技术指标必须选择 1 至 3 项")
        if len(set(values)) != len(values):
            raise ValueError("技术指标不能重复")
        unknown = [value for value in values if value not in self.MODES]
        if unknown:
            raise ValueError(f"未知技术指标：{', '.join(unknown)}")
        self._updating = True
        try:
            for mode, action in self._actions.items():
                action.setChecked(mode in values)
        finally:
            self._updating = False
        self._sync_state()
        if emit:
            self.selection_changed.emit(self.selected_indicators)

    def _action_toggled(self, mode: str, _: bool) -> None:
        if self._updating:
            return
        selected = self.selected_indicators
        if not 1 <= len(selected) <= self.MAX_SELECTED:
            self.set_selected(selected or (mode,))
            return
        self._sync_state()
        self.selection_changed.emit(selected)

    def _sync_state(self) -> None:
        selected = self.selected_indicators
        count = len(selected)
        for action in self._actions.values():
            checked = action.isChecked()
            action.setEnabled(not ((checked and count == 1) or (not checked and count >= 3)))
        self.setText(f"{' · '.join(selected)}  ({count}/3)")
        self.setAccessibleName(f"技术指标多选，已选 {count} 项")
        self.setAccessibleDescription("、".join(selected))
