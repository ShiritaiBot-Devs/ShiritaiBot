插件经常需要用户根据实际情况动态填写一些配置，例如API Key、轮询间隔、要监听的群号……你只需要在插件目录放一个 `_conf_schema.json`，管理后台的插件设置里就会出现对应的表单。

ShiritaiBot的插件配置文件格式和 AstrBot 相同。如果你正在进行AstrBot到ShiritaiBot的插件迁移，可以直接复制原有的JSON结构。

## 声明配置项 {#schema}

```json _conf_schema.json
{
  "api_key": {
    "type": "secret",
    "description": "API Key",
    "hint": "在天气服务的控制台获取",
    "secret": true
  },
  "default_city": {
    "type": "string",
    "description": "默认城市",
    "default": "北京"
  },
  "unit": {
    "type": "string",
    "description": "温度单位",
    "options": ["c", "f"],
    "labels": ["摄氏度", "华氏度"],
    "default": "c"
  },
  "interval": {
    "type": "int",
    "description": "刷新间隔（秒）",
    "slider": { "min": 60, "max": 3600, "step": 60 },
    "default": 600
  }
}
```

每个键是配置项的名字，值描述这个配置项。最常用的是 `type`、`description`、`hint` 和 `default`，这四个字段分别设置配置项的类型、描述、提示文本和默认值。

## 配置项类型 {#types}

| type | 说明 | 未填 default 时的默认值 |
| --- | --- | --- |
| `string` | 单行文本 | `""` |
| `text` | 多行文本 | `""` |
| `int` | 整数 | `0` |
| `float` | 小数 | `0.0` |
| `bool` | 开关 | `false` |
| `list` | 字符串列表 | `[]` |
| `object` | 一组子配置项，用 `items` 声明 | 各子项的默认值 |
| `dict` | 自由填写的键值对 | `{}` |
| `template_list` | 按模板添加的条目列表 | `[]` |

下面是一个 `object` 类型字段的写法示例：

```json _conf_schema.json
{
  "advanced": {
    "type": "object",
    "description": "高级设置",
    "items": {
      "retries": { "type": "int", "description": "重试次数", "default": 3 },
      "verbose": { "type": "bool", "description": "详细日志" }
    }
  }
}
```

如果你需要在config里面读取 `retries` 字段，则通过 `self.config["advanced"]["retries"]` 读取（见下）。

## 常用选项 {#options}

| 选项 | 说明 |
| --- | --- |
| `description` | 表单上显示的名称 |
| `hint` | 名称下方的帮助文字；加上 `"obvious_hint": true` 会醒目显示 |
| `default` | 默认值 |
| `options` / `labels` / `subtitles` | 下拉选项、选项显示名、选项说明，按顺序一一对应。`list` 类型带 `options` 时变成多选 |
| `slider` | 给 `int` / `float` 加滑块：`{"min": 0, "max": 10, "step": 1}` |
| `secret` | 密钥字段，见下文 |
| `invisible` | 不在表单中显示，但值会保留，适合插件自己维护的数据 |
| `editor_mode` / `editor_language` | 用代码编辑框显示，适合填 JSON、正则之类的内容 |
| `minimum` / `maximum` / `pattern` | 数值范围和正则校验，不符合时插件不会加载 |

`dict` 的 `template_schema`、`template_list` 的 `templates` 用法与 AstrBot 一致。暂时不支持 `file` 类型。

## 读取配置 {#read}

`self.config` 是一个字典，里面是默认值和用户填写的值合并后的结果：

```python main.py
class Weather(Plugin):
    async def initialize(self):
        self.city = self.config["default_city"]
        self.interval = self.config["interval"]
```

用户在管理后台保存配置后，插件会自动重载，新实例拿到的就是新配置。所以在 `initialize()` 里读取配置就可以了，不需要自己监听配置变化。

如果配置不符合定义（比如超出了 `maximum`），插件不会加载，插件列表里会显示出问题的字段名。

## 密钥字段 {#secret}

设置了 `"secret": true` 的字段（只能用于 `string` 或 `list`）会加密保存，管理后台不会回显已保存的值。

插件读到的不是明文，而是一个形如 `secret:...` 的引用，需要用 `resolve_secret()` 换成真正的值：

```python main.py
async def initialize(self):
    self.api_key = await self.context.resolve_secret(self.config["api_key"])
```

**拿到明文之后，请只在插件内部使用，不要打日志，不要返回给模型，也不要放进子进程的环境变量。**

## 在代码里修改配置 {#save}

插件可以修改自己的配置并写回：

```python main.py
self.config["last_sync"] = "2026-11-03T09:00:00+08:00"
await self.config.save_config()
```

`save_config()` 只保存，不会触发重载，当前实例会继续使用这个 `self.config` 对象。

如果你要存的是运行中产生的数据，而不是用户需要看到或修改的设置，用 [KV 存储](/docs/plugins/context#storage) 更合适。

## 更新配置项 {#update}

插件升级时可以直接修改 `_conf_schema.json`。重载后，新增的配置项会自动补上默认值，删掉的配置项会被移除，用户已经填写的其他值保持不变。
