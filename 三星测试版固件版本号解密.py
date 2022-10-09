from genericpath import exists
import time
from xml.sax import make_parser
import requests
import hashlib
from lxml import etree
import os
import random
from datetime import datetime
from datetime import timezone
from datetime import timedelta
import json
from functools import cmp_to_key
import telegram
from copy import deepcopy


# 本地是使用代理
if(exists('debug')):
    os.environ["http_proxy"] = "http://127.0.0.1:7890"
    os.environ["https_proxy"] = "http://127.0.0.1:7890"


def getModelDicts():
    # 三星固件代号解释:如"N9860ZCU3FVG3"
    # N9860:表示机型代号 ZC:大陆公开版 U3:U表示user,还有另一个S，3表示防止降级的版本
    # F:表示更新了6个版本，从A开始算
    # VG3:当前版本发布时间，V表示2022年(A表示2000),G表示7月(A表示1月),3表示第3个版本
    ModelDic = {
        # 机型代号字典，格式为"设备代号:{型号，名称}"
        "SM-N9760": {'model': "CHC", 'name': 'Note 10+'},
        "SM-N9860": {'model': "CHC", 'name': 'Note 20 Ultra'},
        "SM-T970": {'model': "CHN", 'name': 'Tab S7+'},
        "SM-G9810": {'model': "CHC", 'name': 'S20'},
        "SM-G9860": {'model': "CHC", 'name': 'S20+'},
        "SM-G9880": {'model': "CHC", 'name': 'S20 Ultra'},
        "SM-G9960": {'model': "CHC", 'name': 'S21+'},
        "SM-G9980": {'model': "CHC", 'name': 'S21 Ultra'},
        "SM-S9010": {'model': "CHC", 'name': 'S22'},
        "SM-S9060": {'model': "CHC", 'name': 'S22+'},
        "SM-S9080": {'model': "CHC", 'name': 'S22 Ultra'}
    }
    return ModelDic


def requestXML(url):
    UA_list = [
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36 Edg/107.0.0.0",
        "Mozilla/5.0 (Linux; Android 9; SAMSUNG SM-T825Y) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/15.0 Chrome/90.0.4430.210 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.186 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/62.0.3202.62 Safari/537.36",
        "Mozilla/5.0 (Linux; U; Android 8.1.0; zh-cn; vivo X20A Build/OPM1.171019.011) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/77.0.3865.120 MQQBrowser/12.0 Mobile Safari/537.36 COVC/045730",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 11_0_3 like Mac OS X) AppleWebKit/604.3.5 (KHTML, like Gecko) Version/11.0 MQQBrowser/11.8.3 Mobile/15B87 Safari/604.1 QBWebViewUA/2 QBWebViewType/1 WKType/1",
        "Mozilla/5.0 (Macintosh; U; PPC Mac OS X 10.5; en-US; rv:1.9.2.15) Gecko/20110303 Firefox/3.6.15"]
    headers = {'User-Agent': random.choice(UA_list), 'Connection': 'close'}
    content = requests.get(url, headers=headers).content
    return content


# 获取官网版本号代码的md5值
def readXML(model):
    modelDic = getModelDicts()
    md5list = []
    url = (
        "https://fota-cloud-dn.ospserver.net/firmware/"
        + modelDic[model]['model']
        + "/"
        + model
        + "/version.test.xml"
    )
    content = requestXML(url)
    xml = etree.fromstring(content)
    for node in xml.xpath("//value//text()"):
        md5list.append(node)
    return md5list

# 暴力解密版本号


def DecryptionFirmware(md5list, model):
    modelDic = getModelDicts()
    url = (
        "https://fota-cloud-dn.ospserver.net/firmware/"
        + modelDic[model]['model']
        + "/"
        + model
        + "/version.xml"
    )
    content = requestXML(url)
    xml = etree.fromstring(content)
    latestVer = xml.xpath("//latest//text()")[0].split('/')  # 获取当前最新的AP和CSC版本号
    ApCode = latestVer[0][:-6]  # 如：N9860ZC
    CscCode = latestVer[1][:-5]  # 如：N9860OZL
    Dicts = {}
    Dicts[model] = {}
    Dicts[model]['versions'] = {}
    Dicts[model]['latestVersion'] = ''
    DecDicts = {}
    tempCpVersions = []
    VerFilePath = 'firmware.json'
    lastVersion = ''
    startUpdateCount = 65  # 设置版本号中更新次数为A，即1次
    startYear = 65  # 设置版本号中更新年份为A，即2000年
    startMonth = 65   # 设置版本号中更新月份为A，即2000年
    startJJ = 1
    with open(VerFilePath, 'r', encoding='utf-8') as f:
        jsonStr = f.read()
        oldJson = {}
        if(jsonStr != ''):
            oldJson = json.loads(jsonStr)
        if(model in oldJson.keys() and 'latestVersion' in oldJson[model].keys()):
            lastVersion = oldJson[model]['latestVersion'].split('/')[0]
    if(lastVersion != ''):
        startJJ = int(lastVersion[-5])
        startUpdateCount = ord(lastVersion[-4])
        startYear = ord(lastVersion[-3])
        startMonth = ord(lastVersion[-2])
    starttime = time.perf_counter()
    for i1 in "US":
        jjNumber = int(latestVer[0][-5])+1
        for j1 in range(startJJ, jjNumber):  # 防止降级的版本
            updateCount = ord(latestVer[0][-4])+1  # 获取更新次数,倒数第4位
            for k1 in range(startUpdateCount, updateCount):
                curYear = ord(latestVer[0][-3])+1  # 获取当前年份，,倒数第3位
                for l1 in range(startYear, curYear):  # A表示2000年，后面递增
                    for m1 in range(startMonth, 77):  # A-L表示1-12月
                        for n1 in "123456789ABCDEFGHIJK":
                            vc = str(j1) + chr(k1) + \
                                chr(l1) + chr(m1) + n1  # 版本号
                            CpCode = ''if latestVer[2] == '' else ApCode + i1 + vc
                            version1 = ApCode + i1 + vc + "/" + CscCode + vc + "/"+CpCode
                            if(model in oldJson.keys() and 'versions' in oldJson[model].keys()) and version1 in oldJson[model]['versions'].values():
                                continue
                            md5 = hashlib.md5()
                            md5.update(version1.encode(encoding="utf-8"))
                            # 基带和固件版本一致时
                            if md5.hexdigest() in md5list:
                                DecDicts[md5.hexdigest()] = version1
                                if(version1.split('/')[2] != ''):
                                    tempCpVersions.append(
                                        version1.split('/')[2])
                            # 基带和固件版本不一致时
                            if(latestVer[2] != '' and tempCpVersions != ''):
                                for tempCpVersion in tempCpVersions[-6:]:
                                    version2 = ApCode + i1 + vc + "/" + CscCode + vc + "/"+tempCpVersion
                                    md5 = hashlib.md5()
                                    md5.update(version2.encode(
                                        encoding="utf-8"))
                                    if md5.hexdigest() in md5list:
                                        DecDicts[md5.hexdigest()] = version2
    if len(DecDicts.values()) > 0:
        Dicts[model]['latestVersion'] = sorted(
            DecDicts.values(), key=lambda x: x.split('/')[0][-4:])[-1]  # 记录最新版本号
        Dicts[model]['versions'] = DecDicts
    endtime = time.perf_counter()
    print("机型：" + model + " 测试版固件版本号解密完毕," +
          '耗时:', round(endtime - starttime, 2), '秒')
    if len(DecDicts) > 0:
        print(model+' 此次解密'+str(len(DecDicts))+'个测试版本号')
    else:
        print(model+' 无新增!')
    return Dicts

# 使用TG机器人推送


def telegram_bot(title: str, content: str) -> None:

    if not push_config.get("TG_BOT_TOKEN") or not push_config.get("TG_USER_ID"):
        print("tg 服务的 bot_token 或者 user_id 未设置!!\n取消推送")
        return
    print("tg 服务启动")
    bot = telegram.Bot(token=push_config.get("TG_BOT_TOKEN"))
    bot.send_message(chat_id=push_config.get("TG_USER_ID"),
                     text=f"{title}\n\n{content}",
                     parse_mode=telegram.ParseMode.MARKDOWN,
                     disable_web_page_preview='true')


# 使用FCM推送
def fcm(title: str, content: str, link: str) -> None:

    # url
    # data2 = {"title": title, "url": link}
    # data1 = {"link": data2}

    # TEXT
    data2 = {"title": title, "message": content}
    data1 = {"text": data2}

    data = {"to": push_config.get(
        "FCM_KEY"), "time_to_live": 60, "priority": "high", "data": data1}
    headers = {'authorization': 'key=AAAASwElybY:APA91bFaTT_zKLcLYqB0soW8PJmFFG7x1F3wiR0MGta9lLsU22uAVa0VD_3zzz-OremJKDEWEf52OD554byamcwAmZldgrQKfwAjjbhZz_5DYT-z1gcflUBFSWVQQ9lSE9KwDBNHULvfVKmQwxa7xNwuPHz-VfdTbw', 'Content-Type': 'application/json'}
    url = "https://fcm.googleapis.com/fcm/send"
    response = requests.post(url=url, data=json.dumps(data), headers=headers)
    print(response)


push_config = {
    'FCM_API_KEY': '',
    'FCM_KEY': '',
    'PUSH_KEY': '',                     # server 酱的 PUSH_KEY，兼容旧版与 Turbo 版
    # 必填 tg 机器人的 TG_BOT_TOKEN，例：1407203283:AAG9rt-6RDaaX0HBLZQq0laNOh898iFYaRQ
    'TG_BOT_TOKEN': '',
    'TG_USER_ID': '',                   # 必填 tg 机器人的 TG_USER_ID，例：1434078534
    'TG_API_HOST': '',                  # tg 代理 api
    'TG_PROXY_AUTH': '',                # tg 代理认证参数
    'TG_PROXY_HOST': '',                # tg 机器人的 TG_PROXY_HOST
    'TG_PROXY_PORT': '',                # tg 机器人的 TG_PROXY_PORT
}


if __name__ == '__main__':
    # 获取相关参数变量数据
    for k in push_config:
        if os.getenv(k):
            v = os.getenv(k)
            push_config[k] = v
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
    decDicts = {"上次更新时间": now}
    VerFilePath = 'firmware.json'
    AddTxtPath = '测试版新增日志.txt'
    verDic = {}
    with open(VerFilePath, 'r', encoding='utf-8') as f:
        jsonStr = f.read()
        oldJson = {}
        if(jsonStr != ''):
            oldJson = json.loads(jsonStr)
        hasNewVersion = False
        for model in modelDic:
            md5list = readXML(model)
            newMDic = {}
            if model in oldJson.keys():
                newMDic[model] = deepcopy(oldJson[model])
            else:
                newMDic[model] = {}
                newMDic[model]['versions'] = {}
                newMDic[model]['latestVersion'] = ''
            decDicts.update(newMDic)  # 先保存已有的数据
            verDic = DecryptionFirmware(md5list, model)  # 解密获取新数据
            diffModel = verDic[model]['versions'].keys()
            if verDic[model]['latestVersion'] != '':
                newMDic[model]['latestVersion'] = verDic[model]['latestVersion']
            if len(newMDic[model]['versions'].keys()) > 0:
                diffModel = verDic[model]['versions'].keys(
                )-newMDic[model]['versions'].keys()

            if len(diffModel) > 0:
                hasNewVersion = True
                for key in diffModel:
                    # 存入新的版本号
                    newMDic[model]['versions'][key] = verDic[model]['versions'][key]
        newMDic
        decDicts.update(newMDic)
        if hasNewVersion:
            # 固件更新日志
            with open(AddTxtPath, 'a+', encoding='utf-8') as file:
                file.write("*****记录时间:" + now + "*****\n")
                for model in modelDic:
                    if (model in decDicts) and (model in oldJson):
                        md5Keys = decDicts[model]['versions'].keys(
                        ) - oldJson[model]['versions'].keys()  # 获取新增的版本号
                        if len(md5Keys) > 0:
                            textStr = ''
                            Str = ''
                            for md5Key in md5Keys:
                                VerStr = str(
                                    decDicts[model]['versions'][md5Key])
                                textStr += "\n"+VerStr
                                Str += modelDic[model]['name'] + "新增测试固件版本：" + \
                                    VerStr + "，对应MD5值：" + md5Key + "\n"
                            file.write(Str)
                            fcm(modelDic[model]['name']+"新增内测固件",
                                textStr.replace('*', ''), '')
                            telegram_bot('#'+modelDic[model]['name']
                                         + "新增内测固件", textStr)
        # 更新全机型最新版
        with open('各机型最新版本.md', 'w', encoding='utf-8') as f:
            modelDic = getModelDicts()
            textStr = ''
            for model in modelDic.keys():
                textStr += "*"+modelDic[model]['name']+"：*\n" + \
                    decDicts[model]['latestVersion']+'\n'
            f.write(textStr)
            fcm("各机型最新测试版", textStr.replace('*', ''), '')
            telegram_bot("#各机型最新测试版\n", textStr)
    with open(VerFilePath, 'w', encoding='utf-8') as f:
        f.write(json.dumps(decDicts, indent=4, ensure_ascii=False))
