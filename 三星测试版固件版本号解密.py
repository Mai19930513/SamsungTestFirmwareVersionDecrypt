from sys import path
import requests
import hashlib
from lxml import etree
import os
import random
from datetime import datetime
from datetime import timezone
from datetime import timedelta
import json


def getModelDicts():
    # 机型代号字典，格式为<设备代号":["固件前缀","系统版本前缀","地区代码"]>
    ModelDic = {
        "SM-N9760": ["N9760ZC", "N9760CHC", "CHC"],
        "SM-N9860": ["N9860ZC", "N9860OZL", "CHC"],
        "SM-T970": ["T970ZC", "T970CHN", "CHN"],
        "SM-G9960": ["G9960ZC", "G9960CHC", "CHC"],
        "SM-G9860": ["G9860ZC", "G9860OZL", "CHC"],
        "SM-G9980": ["G9980ZC", "G9980CHC", "CHC"],
        "SM-S9010": ["S9010ZC", "S9010CHC", "CHC"],
        "SM-S9060": ["S9060ZC", "S9060CHC", "CHC"],
        "SM-S9080": ["S9080ZC", "S9080CHC", "CHC"],
    }
    return ModelDic


def readXML(model):
    md5list = []
    UA_list = [
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/67.0.3396.99 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; WOW64) Gecko/20100101 Firefox/61.0",
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.186 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/62.0.3202.62 Safari/537.36",
        "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.101 Safari/537.36",
        "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.0)",
        "Mozilla/5.0 (Macintosh; U; PPC Mac OS X 10.5; en-US; rv:1.9.2.15) Gecko/20110303 Firefox/3.6.15",
    ]
    headers = {'User-Agent': random.choice(UA_list), 'Connection': 'close'}
    url = (
        "https://fota-cloud-dn.ospserver.net/firmware/"
        + modelDic[model][2]
        + "/"
        + model
        + "/version.test.xml"
    )
    content = requests.get(url, headers=headers).content
    xml = etree.fromstring(content)
    for node in xml.xpath("//value//text()"):
        md5list.append(node)
    return md5list


def DecryptionFirmware(md5list, model):
    modelDic = getModelDicts()
    Dicts = {}
    DecDicts = {}
    BlModel = modelDic[model][0]
    VerModel = modelDic[model][1]
    for i1 in "US":
        for j1 in "123":
            for k1 in range(65, 91):
                for l1 in range(65, 91):
                    for m1 in range(65, 91):
                        for n1 in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                            vc = j1 + chr(k1) + chr(l1) + chr(m1) + n1
                            if modelDic[model][2] == "CHN":
                                version = BlModel + i1 + vc + "/" + VerModel + vc + "/"
                            else:
                                version = (
                                    BlModel
                                    + i1
                                    + vc
                                    + "/"
                                    + VerModel
                                    + vc
                                    + "/"
                                    + BlModel
                                    + i1
                                    + vc
                                )
                            md5 = hashlib.md5()
                            md5.update(version.encode(encoding="utf-8"))
                            if md5.hexdigest() in md5list:
                                DecDicts[md5.hexdigest()] = version
    print("机型：" + model + " 测试版固件版本号解密完毕*************\n")
    Dicts[model] = DecDicts
    return Dicts


if __name__ == '__main__':
    modelDic = getModelDicts()
    jsonStr = ""
    SHA_TZ = timezone(
        timedelta(hours=8),
        name='Asia/Shanghai',
    )
    now = (
        datetime.utcnow()
        .replace(tzinfo=timezone.utc)
        .astimezone(SHA_TZ)
        .strftime('%Y-%m-%d %H:%M')
    )
    decDicts = {"update": now}
    VerFilePath = 'firmware.json'
    AddTxtPath = '测试版新增日志.txt'
    verDic = {}
    AddVer = {"update": now}
    with open(VerFilePath, 'r') as f:
        jsonStr = f.read()
        oldJson = json.loads(jsonStr)
        for model in modelDic:
            md5list = readXML(model)
            verDic = DecryptionFirmware(md5list, model)
            decDicts.update(verDic)
        file = open(AddTxtPath, 'a+')
        file.write("*****记录时间:" + now + "*****\n")
        for model in modelDic:
            if (model in decDicts) & (model in oldJson):
                md5Keys = decDicts[model].keys() - oldJson[model].keys()
                if len(md5Keys) > 0:
                    for md5Key in md5Keys:
                        VerStr = str(decDicts[model][md5Key])
                        file.write(
                            model + "新增测试固件版本：" + VerStr + "，对应MD5值：" + md5Key + "\n"
                        )
        file.close()
    with open(VerFilePath, 'w') as f:
        f.write(json.dumps(decDicts))
