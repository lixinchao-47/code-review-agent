# 面试知识点

## 1. 密钥管理链：.env → dotenv → os → config

四个组件的分工与协作：

```
.env 文件（磁盘上的纯文本，不进 Git）
    ↓   load_dotenv() 读取并注入
os.environ（操作系统环境变量表，内存中的字典）
    ↓   os.getenv() 读取
config.py 中的 Python 常量（其余模块直接 import 使用）
```

| 组件 | 是什么 | 职责 |
|------|--------|------|
| `.env` | 项目根目录下的纯文本文件，`KEY=VALUE` 格式 | 存密钥和配置值，`.gitignore` 排除，不泄露到 GitHub |
| `python-dotenv` | 第三方 pip 包 | 读取 `.env` 文件，把键值对注入 `os.environ` |
| `load_dotenv()` | `python-dotenv` 提供的函数 | 在 `config.py` 被 import 时调用一次，触发注入 |
| `os` | Python 内置标准库 | 通过 `os.getenv()` 从环境变量表中读取值 |
| `config.py` | 项目统一配置入口 | 调用 `load_dotenv()` → 读环境变量 → 做类型转换和默认值 → 导出为 Python 常量，其他模块 `from config import XXX` 直接用 |

Docker 部署时，`.env` 通过 `env_file: .env` 注入容器，镜像本身不含密钥。

