# 用户指南

## 安装与启动

桌面应用面向 Windows，需要 Python 3.11 至 3.14、FFmpeg 和 FFprobe。将 FFmpeg 和 FFprobe 加入 `PATH`，在项目根目录运行：

```powershell
python -m pip install .
python -m mdict_audio_app.main --data-dir data
```

## 生成生词音频

1. 在 **Dictionaries** 页面点击 **Import MDX**，选择一个或多个 `.mdx` 文件。程序会自动匹配同名 `.mdd`；未匹配时可手动选择。导入完成后，可用 **Move Up** 和 **Move Down** 调整词典优先级。
2. 在 **Words & Audio** 页面逐行粘贴生词，或点击 **Import TXT/CSV** 导入 UTF-8 文件。CSV 使用第一列作为生词。
3. 设置例句数量、静音间隔、最长时间和输出格式，点击 **Preview** 查看命中与跳过情况。
4. 填写输出路径，点击 **Generate audio**。程序生成 MP3、M4A 或 WAV 音频，并在同一目录生成同名 `.json` 清单。若目标文件已存在，程序会询问是否覆盖。

缺少可用发音的生词会被跳过。`--data-dir` 指定词典索引和音频资源的保存目录。

## 单独提取词典

如需将 MDX 词条提取为文本文件，在项目根目录运行：

```powershell
python -m mdict_utils.readmdict --help
python -m mdict_utils.readmdict -x path/to/dictionary.mdx
```

若存在同名 MDD 文件，资源会写入词典旁的 `data` 目录。`python -m pip install .` 会安装读取 MDict 引擎 1.2 的 LZO 压缩文件所需的 `python-lzo`。该依赖在 Windows 上提供 Python 3.11 的预编译安装包；其他受支持的 Python 版本可能需要本机编译环境。

仅导入和提取有权使用的词典。
