\# 模块接入规范 v1



\## 一、支持模块类型

平台当前支持两类模块：



\- native：已编译的本地可执行模块（如 exe）

\- python：Python 脚本模块（如 main.py）



\---



\## 二、模块包上传格式

模块必须以 zip 上传，zip 内必须包含模块根目录。



\### 1. native 模块结构

module.zip

└─ module/

&#x20;  ├─ module.json

&#x20;  ├─ app.exe

&#x20;  ├─ \*.dll

&#x20;  ├─ README.txt

&#x20;  └─ sample/



\### 2. python 模块结构

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

\- name：模块显示名称

\- description：模块说明

\- runtime：native 或 python

\- entry：入口文件名



\---



\## 四、module.json 推荐字段



\- config\_mode

\- command\_template

\- inputs

\- tags

\- requirements\_file（python 模块可选）



\### config\_mode

\- none

\- json\_file



\### command\_template 示例



\#### native + json\_file

\["{executable}", "{config\_path}"]



\#### python + json\_file

\["{executable}", "main.py", "{config\_path}"]



\---



\## 五、inputs 规范

每个输入字段包含：



\- key

\- label

\- type

\- required

\- placeholder（可选）

\- default（可选）

\- help\_text（可选）



\### type 支持

\- text

\- textarea

\- number

\- file\_path

\- dir\_path

\- password



\---



\## 六、运行规则



\### native 模块

\- 必须上传已编译完成的模块

\- 平台不负责编译

\- 如依赖 DLL，需随模块包一起提供



\### python 模块

\- 平台自动创建独立 venv

\- 若存在 requirements.txt，则自动安装依赖

\- 模块运行时使用自己的 venv



\---



\## 七、推荐原则

平台不接收“随便的源码目录”，只接收“符合规范的模块包”。



这样可以保证：

\- 模块安装流程可控

\- 模块运行方式统一

\- 平台底层代码无需频繁改动

