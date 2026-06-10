from tortoise import fields
from tortoise.models import Model


class StorageAdapter(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100, unique=True)
    type = fields.CharField(max_length=30)
    config = fields.JSONField()
    enabled = fields.BooleanField(default=True)
    path = fields.CharField(max_length=255, unique=True)
    sub_path = fields.CharField(max_length=1024, null=True)

    class Meta:
        table = "storage_adapters"


class Configuration(Model):
    id = fields.IntField(pk=True)
    key = fields.CharField(max_length=100, unique=True)
    value = fields.TextField()

    class Meta:
        table = "configurations"


class AutomationTask(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100)
    event = fields.CharField(max_length=50)

    trigger_config = fields.JSONField(null=True)

    processor_type = fields.CharField(max_length=100)
    processor_config = fields.JSONField()

    enabled = fields.BooleanField(default=True)

    class Meta:
        table = "automation_tasks"


class Plugin(Model):
    id = fields.IntField(pk=True)
    key = fields.CharField(max_length=100, unique=True)  # 插件唯一标识
    name = fields.CharField(max_length=255, null=True)
    version = fields.CharField(max_length=50, null=True)
    description = fields.TextField(null=True)
    author = fields.CharField(max_length=255, null=True)
    website = fields.CharField(max_length=2048, null=True)
    github = fields.CharField(max_length=2048, null=True)
    license = fields.CharField(max_length=100, null=True)

    # 完整 manifest 存储
    manifest = fields.JSONField(null=True)

    # 前端相关配置（从 manifest.frontend 提取）
    open_app = fields.BooleanField(default=False)
    supported_exts = fields.JSONField(null=True)
    default_bounds = fields.JSONField(null=True)
    default_maximized = fields.BooleanField(null=True)
    icon = fields.CharField(max_length=2048, null=True)

    # 已加载的组件追踪
    loaded_routes = fields.JSONField(null=True)  # ["/api/plugins/xxx", ...]
    loaded_processors = fields.JSONField(null=True)  # ["processor_type", ...]

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "plugins"
