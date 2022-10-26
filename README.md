[![生成固件解密json文件](https://github.com/Mai19930513/SamsungTestFirmwareVersionDecrypt/actions/workflows/python-app.yml/badge.svg)](https://github.com/Mai19930513/SamsungTestFirmwareVersionDecrypt/actions/workflows/python-app.yml)
# 主要功能
通过生成序列号穷举MD5加密后的结果与官网对比，达到解密版本号的功能。
三星测试版固件官网:`https://fota-cloud-dn.ospserver.net/firmware/区域码/设备型号/version.test.xml`,示例网站：如[国行版Note20 Ultra](https://fota-cloud-dn.ospserver.net/firmware/CHC/SM-N9860/version.test.xml)
# 如何添加自己需要的设备
修改`models.txt`，添加格式为`设备名称,设备型号,地区代码(多个地区用|分隔)`
