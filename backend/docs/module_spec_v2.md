\# 模块接入规范 v2



\## 一、支持的模块类型



平台当前支持两类模块：



\- native：已编译的本地可执行模块（exe）

\- python：Python 脚本模块（main.py）



\---



\## 二、上传格式



模块必须以 zip 上传，zip 内必须包含模块根目录。



\### 1. native 模块推荐结构



module.zip

└─ module/

&#x20;  ├─ module.json

&#x20;  ├─ app.exe

&#x20;  ├─ README.txt

&#x20;  ├─ deps/          # 推荐：依赖文件夹

&#x20;  ├─ bin/           # 可选：依赖文件夹

&#x20;  ├─ dlls/          # 可选：依赖文件夹

&#x20;  ├─ libs/          # 可选：依赖文件夹

&#x20;  └─ sample/        # 可选：示例配置



\### 2. python 模块推荐结构



module.zip

└─ module/

&#x20;  ├─ module.json

&#x20;  ├─ main.py

&#x20;  ├─ requirements.txt

&#x20;  ├─ README.txt

&#x20;  └─ sample/



\---



\## 三、module.json 必填字段



\- id

\- name

\- description

\- runtime

\- entry



\### 字段说明



\- id：模块唯一标识，建议小写英文数字下划线

\- name：模块名称

\- description：模块说明

\- runtime：native 或 python

\- entry：入口文件名



\---



\## 四、native 模块依赖模式



native 模块支持以下 dependency\_mode：



\### 1. embedded\_folder（推荐）

用户把依赖放进模块目录里的依赖文件夹，平台自动识别并复制到入口程序同目录。



支持自动扫描的依赖目录名：



\- deps

\- bin

\- dlls

\- libs

\- runtime

\- redist

\- dependencies

\- third\_party



\### 2. self\_contained

模块已经自包含，平台不做额外依赖处理。



\### 3. manual\_bundle

用户手工把所有依赖直接打在模块根目录或自己管理，平台不自动补齐。



\### 4. msys2\_auto

平台尝试从 MSYS2 环境自动收集 DLL。

此模式只适用于明确在 MSYS2 / UCRT64 / MINGW64 / CLANG64 下编译的模块。



\---



\## 五、建议用户去哪里找依赖文件夹



\### A. 如果是 MSYS2 / UCRT64 / MINGW64 / CLANG64 编译

常见目录：



\- C:/msys64/ucrt64/bin

\- C:/msys64/mingw64/bin

\- C:/msys64/clang64/bin

\- C:/msys64/usr/bin



如果不知道用的是哪种环境，可以在对应终端里执行：



\- ldd app.exe

\- ntldd app.exe



然后把非系统目录里的 DLL 复制到模块 zip 的 deps/ 目录。



\### B. 如果是 Visual Studio / MSVC 编译

常见依赖来源：



\- 项目输出目录下已有的 dll

\- 第三方 SDK 的 bin 目录

\- vcpkg 安装目录下的 installed/<triplet>/bin

\- 手工集成库的 bin 目录

\- 程序能在普通 cmd 里运行时所在目录中的所有非系统 dll



典型位置示例：



\- D:/your\_project/build/Release

\- D:/your\_project/x64/Release

\- C:/vcpkg/installed/x64-windows/bin

\- C:/Program Files/xxx/bin



\### C. 如果是 Qt 程序

常见依赖来源：



\- Qt 安装目录的 bin

\- 项目构建输出目录

\- windeployqt 生成的发布目录



典型位置示例：



\- C:/Qt/6.8.0/msvc2022\_64/bin

\- C:/Qt/5.15.2/mingw81\_64/bin

\- 项目 release 目录



\### D. 如果是 Python 打包出来的 exe

常见依赖来源：



\- PyInstaller 生成目录

\- Nuitka 输出目录

\- 程序打包目录中的所有动态库



\### E. 如果不知道该找哪些文件

建议先在“普通 Windows cmd”里测试：



app.exe config.json



如果提示缺少某个 dll，就把该 dll 及其同级依赖一起放入 deps/。

如果在本机某个目录能运行成功，最稳妥的方式就是把那个目录里 app.exe 所需的非系统文件一起放进模块包。



\---



\## 六、平台自动识别哪些依赖文件



平台在依赖目录中自动识别以下文件类型，并复制到入口程序同目录：



\- \*.dll

\- \*.exe

\- \*.pyd

\- \*.manifest



\---



\## 七、command\_template 示例



\### native + json\_file

\["{executable}", "{config\_path}"]



\### python + json\_file

\["{executable}", "main.py", "{config\_path}"]



\---



\## 八、inputs 规范



每个输入字段包含：



\- key

\- label

\- type

\- required

\- placeholder（可选）

\- default（可选）

\- help\_text（可选）



支持的 type：



\- text

\- textarea

\- number

\- file\_path

\- dir\_path

\- password



\---



\## 九、运行规则



\### native

\- 平台不负责编译源码

\- 建议上传已编译模块

\- 推荐使用 embedded\_folder，把依赖放进 deps/ 或 bin/



\### python

\- 平台自动创建独立 venv

\- 若存在 requirements.txt，则自动安装依赖

\- 运行时使用模块自己的 venv

