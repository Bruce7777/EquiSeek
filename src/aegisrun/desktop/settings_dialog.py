from __future__ import annotations

from PySide6.QtCore import QSettings, QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from aegisrun.desktop.credentials import CredentialStore
from aegisrun.desktop.workers import DeepSeekConnectionTask
from aegisrun.research.deepseek import (
    DEEPSEEK_OFFICIAL_MODEL_OPTIONS,
    normalize_deepseek_model,
)


class SettingsDialog(QDialog):
    def __init__(
        self, credentials: CredentialStore, settings: QSettings, parent: object = None
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.credentials = credentials
        self.settings = settings
        self.thread_pool = QThreadPool.globalInstance()
        self._connection_tasks: set[DeepSeekConnectionTask] = set()
        self.setWindowTitle("本地连接设置")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "DeepSeek Key 用于 Agent 规划与语言整理；Tushare Token 用于可选行情源；"
            "Tavily Key 仅在 Agent 明确调用联网搜索时使用。手工保存的凭据进入操作系统"
            "密钥库，不写入报告、对话或普通日志。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("mutedText")
        layout.addWidget(intro)
        form = QFormLayout()
        self.deepseek_key = QLineEdit()
        self.deepseek_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.deepseek_key.setPlaceholderText(
            "已配置" if credentials.get_deepseek_api_key() else "DeepSeek API Key"
        )
        self.deepseek_key.setAccessibleName("DeepSeek API Key")
        self.tushare_token = QLineEdit()
        self.tushare_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.tushare_token.setPlaceholderText(
            "已配置" if credentials.get_tushare_token() else "可选 Tushare Token"
        )
        self.tushare_token.setAccessibleName("Tushare Token")
        self.tavily_key = QLineEdit()
        self.tavily_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.tavily_key.setPlaceholderText(
            "已配置" if credentials.get_tavily_api_key() else "可选 Tavily Search Key"
        )
        self.tavily_key.setAccessibleName("Tavily Search Key")
        self.deepseek_model = QComboBox()
        self.deepseek_model.setAccessibleName("DeepSeek 模型")
        self.deepseek_model.setToolTip("Flash 更快更经济；Pro 更适合复杂解释，费用更高")
        for label, model in DEEPSEEK_OFFICIAL_MODEL_OPTIONS:
            self.deepseek_model.addItem(label, model)
        selected_model = normalize_deepseek_model(
            settings.value("research/deepseek_model", DEEPSEEK_OFFICIAL_MODEL_OPTIONS[0][1])
        )
        self.deepseek_model.setCurrentIndex(self.deepseek_model.findData(selected_model))
        self.use_ai = QCheckBox("使用所选 DeepSeek V4 模型整理客观历史事实")
        self.use_ai.setChecked(bool(settings.value("research/use_ai", True, type=bool)))
        self.include_builtin_skills = QCheckBox("启用内置投资 Skill（用户同名 Skill 仍优先）")
        self.include_builtin_skills.setChecked(
            bool(settings.value("skills/include_builtin", True, type=bool))
        )
        self.user_skill_root = QLineEdit(
            str(settings.value("skills/user_root", "~/.equiseek/user-data/skills", type=str))
        )
        self.user_skill_root.setPlaceholderText("~/.equiseek/user-data/skills")
        self.user_skill_root.setAccessibleName("用户 Skill 目录")
        form.addRow("AI · DeepSeek Key", self.deepseek_key)
        form.addRow("行情 · Tushare Token", self.tushare_token)
        form.addRow("联网 · Tavily Key", self.tavily_key)
        form.addRow("AI 模型", self.deepseek_model)
        form.addRow("API 地址", QLabel("https://api.deepseek.com"))
        form.addRow("AI 语言整理", self.use_ai)
        form.addRow("内置 Skill", self.include_builtin_skills)
        form.addRow("用户 Skill 目录", self.user_skill_root)
        self.test_deepseek_button = QPushButton("验证 DeepSeek 连接")
        self.test_deepseek_button.setObjectName("secondaryButton")
        self.test_deepseek_button.clicked.connect(self._test_deepseek)
        form.addRow("连接测试", self.test_deepseek_button)
        layout.addLayout(form)
        self.connection_status = QLabel("尚未执行连接测试")
        self.connection_status.setObjectName("mutedText")
        self.connection_status.setWordWrap(True)
        layout.addWidget(self.connection_status)
        note = QLabel(
            "公开历史数据（BaoStock）无需账号或 Token；离线模拟数据不代表真实证券。"
            "模型只接收结构化指标快照。"
        )
        note.setWordWrap(True)
        note.setObjectName("noticeText")
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _test_deepseek(self) -> None:
        api_key = self.deepseek_key.text().strip()
        if not api_key:
            api_key = self.credentials.get_deepseek_api_key() or ""
        if not api_key:
            self.connection_status.setText("请先输入 DeepSeek API Key。")
            return
        task = DeepSeekConnectionTask(api_key, str(self.deepseek_model.currentData()))
        self._connection_tasks.add(task)
        task.signals.succeeded.connect(
            lambda model, current=task: self._connection_succeeded(current, model)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._connection_failed(current, message)
        )
        self.test_deepseek_button.setEnabled(False)
        self.test_deepseek_button.setText("验证中…")
        self.connection_status.setText("正在连接 DeepSeek 官方 API…")
        self.thread_pool.start(task)

    def _connection_succeeded(self, task: DeepSeekConnectionTask, model: str) -> None:
        self._connection_tasks.discard(task)
        self._finish_connection_test()
        self.connection_status.setText(f"连接成功，当前账户可用模型：{model}")

    def _connection_failed(self, task: DeepSeekConnectionTask, message: str) -> None:
        self._connection_tasks.discard(task)
        self._finish_connection_test()
        self.connection_status.setText(f"连接失败：{message}")

    def _finish_connection_test(self) -> None:
        self.test_deepseek_button.setEnabled(True)
        self.test_deepseek_button.setText("验证 DeepSeek 连接")

    def _save(self) -> None:
        try:
            if self.deepseek_key.text():
                self.credentials.set_deepseek_api_key(self.deepseek_key.text())
            if self.tushare_token.text():
                self.credentials.set_tushare_token(self.tushare_token.text())
            if self.tavily_key.text():
                self.credentials.set_tavily_api_key(self.tavily_key.text())
            self.settings.setValue("research/use_ai", self.use_ai.isChecked())
            self.settings.setValue(
                "skills/include_builtin", self.include_builtin_skills.isChecked()
            )
            self.settings.setValue(
                "skills/user_root",
                self.user_skill_root.text().strip() or "~/.equiseek/user-data/skills",
            )
            self.settings.setValue(
                "research/deepseek_model",
                normalize_deepseek_model(self.deepseek_model.currentData()),
            )
            self.accept()
        except RuntimeError as error:
            QMessageBox.critical(self, "无法保存密钥", str(error))
