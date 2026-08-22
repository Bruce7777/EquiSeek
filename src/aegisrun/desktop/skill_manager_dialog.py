from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class SkillManagerDialog(QDialog):
    """Read-only, local Skill inventory with safe filesystem affordances."""

    def __init__(
        self,
        skills: Sequence[object],
        user_root: Path,
        *,
        refresh: Callable[[], Sequence[object]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._skills = tuple(skills)
        self._user_root = user_root.expanduser().resolve()
        self._refresh_callback = refresh
        self.setWindowTitle("管理 Skill · 求衡")
        self.resize(920, 620)
        self.setMinimumSize(720, 480)
        self._build_ui()
        self.set_skills(self._skills)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        heading = QLabel("Skill 工作区")
        heading.setObjectName("skillManagerTitle")
        root.addWidget(heading)
        note = QLabel(
            "完全本地、无需登录。用户目录中的同名 Skill 优先于内置版本；"
            "Skill 只能声明能力，不能扩大平台工具权限。"
        )
        note.setObjectName("skillManagerNote")
        note.setWordWrap(True)
        root.addWidget(note)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.skill_list = QListWidget()
        self.skill_list.setObjectName("skillManagerList")
        self.skill_list.setAccessibleName("可用 Skill 列表")
        self.skill_list.currentItemChanged.connect(self._show_skill)
        splitter.addWidget(self.skill_list)
        self.detail = QTextBrowser()
        self.detail.setObjectName("skillManagerDetail")
        self.detail.setOpenExternalLinks(False)
        splitter.addWidget(self.detail)
        splitter.setSizes([330, 550])
        root.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setObjectName("skillManagerCount")
        actions.addWidget(self.count_label)
        actions.addStretch()
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh)
        actions.addWidget(refresh_button)
        user_root_button = QPushButton("打开用户 Skill 目录")
        user_root_button.clicked.connect(self._open_user_root)
        actions.addWidget(user_root_button)
        self.copy_button = QPushButton("复制调用命令")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_invocation)
        actions.addWidget(self.copy_button)
        self.open_button = QPushButton("打开 Skill 目录")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_selected)
        actions.addWidget(self.open_button)
        close_button = QPushButton("完成")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        root.addLayout(actions)

    def set_skills(self, skills: Sequence[object]) -> None:
        self._skills = tuple(skills)
        self.skill_list.clear()
        user_count = 0
        for skill in self._skills:
            name = str(getattr(skill, "name", ""))
            provider = str(getattr(skill, "provider", ""))
            is_user = provider.startswith("user-")
            user_count += int(is_user)
            source = "我的 Skill" if is_user else "内置"
            item = QListWidgetItem(f"{name}\n{source} · v{getattr(skill, 'version', '')}")
            item.setData(Qt.ItemDataRole.UserRole, skill)
            self.skill_list.addItem(item)
        self.count_label.setText(f"{len(self._skills)} 个可用 · {user_count} 个用户 Skill")
        if self.skill_list.count():
            self.skill_list.setCurrentRow(0)
        else:
            self.detail.setMarkdown("当前没有启用任何 Skill。")

    def refresh(self) -> None:
        if self._refresh_callback is not None:
            self.set_skills(self._refresh_callback())

    def _show_skill(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        skill = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if skill is None:
            self.copy_button.setEnabled(False)
            self.open_button.setEnabled(False)
            return
        provider = str(getattr(skill, "provider", ""))
        allowed_agents = "、".join(getattr(skill, "allowed_agents", ())) or "未限制"
        allowed_tools = "、".join(getattr(skill, "allowed_tools", ())) or "无额外工具声明"
        network = "需要" if bool(getattr(skill, "network_required", False)) else "不需要"
        package_root = Path(getattr(skill, "package_root", ""))
        self.detail.setMarkdown(
            f"## `{getattr(skill, 'name', '')}`\n\n"
            f"{getattr(skill, 'description', '')}\n\n"
            f"- 来源：`{provider}`\n"
            f"- 版本：`{getattr(skill, 'version', '')}`\n"
            f"- 允许 Agent：{allowed_agents}\n"
            f"- 工具声明：{allowed_tools}\n"
            f"- 联网：{network}\n"
            f"- Manifest：`{str(getattr(skill, 'manifest_sha256', ''))[:16]}`\n"
            f"- 目录：`{package_root}`\n\n"
            "调用方式：在对话开头输入 `/skill-name 你的研究问题`，"
            "或在输入框下方选择本轮 Skill。"
        )
        self.copy_button.setEnabled(True)
        self.open_button.setEnabled(package_root.is_dir())

    def _selected_skill(self) -> object | None:
        item = self.skill_list.currentItem()
        return cast(object, item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _copy_invocation(self) -> None:
        skill = self._selected_skill()
        if skill is not None:
            QApplication.clipboard().setText(f"/{getattr(skill, 'name', '')} ")

    def _open_selected(self) -> None:
        skill = self._selected_skill()
        path = Path(getattr(skill, "package_root", "")) if skill is not None else Path()
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_user_root(self) -> None:
        self._user_root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._user_root)))
