# 依赖与许可证

## 直接依赖

安装：`python -m pip install -r requirements.txt`

| 包 | 最低版本 | 许可证 | 用途 |
|---|---|---|---|
| openai | 1.109.1 | Apache-2.0 | DeepSeek API 客户端（OpenAI 兼容） |
| python-dotenv | 1.2.2 | BSD-3-Clause | 读取 .env 配置 |
| numpy | 1.24 | BSD-3-Clause | 像素级渲染计算 |
| pillow | 10.0 | HPND | 输出 PNG |

## 传递依赖

openai 会拉入传递依赖（anyio、certifi、distro、h11、httpcore、httpx、idna、jiter、pydantic、pydantic-core、sniffio、tqdm、typing-extensions 等），许可证全为宽松（MIT / Apache-2.0 / BSD-3-Clause / PSF-2.0）或文件级弱 copyleft（MPL-2.0：certifi、tqdm）。版本以实际安装为准（`pip show <包>` 或 PyPI）。

## 许可证结论

- 全部依赖为宽松许可证或文件级弱 copyleft，不影响本项目选择 MIT 许可证（与上游 test-agent 一致）。
- DeepSeek API 为服务调用，不构成代码依赖，不产生许可证义务。
- 原始 HTML 的 p5.js 为 LGPL-2.1：本项目新实现用 numpy 复刻 `(dx^dy)&a` 渲染逻辑，不依赖 p5.js。

## 备注

- requirements.txt 只列直接依赖，传递依赖由 pip 自动安装。
- 新增依赖先加进 requirements.txt 再使用；升级依赖后重新核对本表。
