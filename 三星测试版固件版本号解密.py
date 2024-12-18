from genericpath import exists
import concurrent.futures
import time
import requests
import hashlib
from lxml import etree
import os
import random
from datetime import datetime
from datetime import timezone
from datetime import timedelta
import json
import telegram
import pymysql
from copy import deepcopy
from func_timeout import func_set_timeout
import func_timeout
from dotenv import load_dotenv
import string
load_dotenv()



def getConnect():
    '''
    获取数据库连接
    '''
    prefix = os.getenv('PREFIX')
    if prefix != None:
        cert_path = f'{prefix}/etc/tls/cert.pem'
    else:
        cert_path = "/etc/ssl/certs/ca-certificates.crt"
    connection = pymysql.connect(
        host=os.getenv("HOST"),
        user=os.getenv("DBUSER"),
        passwd=os.getenv("PASSWORD"),
        db=os.getenv("DATABASE"),
        charset='utf8mb4',
        port=21777,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor
    )
    return connection

def printStr(str):
    global isDebug
    if isDebug:
        print(str)

def getModelDictsFromDB():
    '''从数据库读取型号'''
    ModelDic = {}
    ModelsQuery = "SELECT * FROM models"
    connect = getConnect()
    cursor = connect.cursor()
    cursor.execute(ModelsQuery)
    models = cursor.fetchall()
    for model in models:
        name=model['name']
        modelCode=model['code']
        countryCode=[]
        for cc in  model['cc'].split('|'):
            countryCode.append(cc)
        ModelDic[modelCode] = {'CC': countryCode, 'name': name}
    return ModelDic

def getModelDicts():
    '''从文件读取型号'''
    ModelDic = {}
    with open('models.txt', 'r', encoding='utf-8') as models:
        for model in models:
            if model.startswith('#'):
                continue
            model = model.strip().split(',')
            name = model[0]
            modelCode = model[1]
            countryCode = []
            for cc in model[2].split('|'):
                countryCode.append(cc)
            ModelDic[modelCode] = {'CC': countryCode, 'name': name}
    return ModelDic


def getCountryName(cc):
    '''
    通过设备代号获取地区名称
    '''
    cc2Country = {'CHC': '国行', 'CHN': '国行', 'TGY': '香港','KOO': '韩国','EUX':'欧洲'}
    if cc in cc2Country.keys():
        return cc2Country[cc]
    else:
        return "地区未知"


def requestXML(url):
    '''
    请求xml内容
    '''
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
    try:
        content = requests.get(url, headers=headers).content
        return content
    except requests.exceptions.ProxyError as e:
        print(f'ProxyError:{e}')
        return None
    except Exception as e:
        print(f"发生错误:{e.args[0]}")


def readXML(model):
    '''
    获取官网版本号代码的md5值
    '''
    global modelDic
    md5Dic = {}
    for cc in modelDic[model]['CC']:
        md5list = []
        url=f"https://fota-cloud-dn.ospserver.net/firmware/{cc}/{model}/version.test.xml"
        content = requestXML(url)
        if content != None:
            xml = etree.fromstring(content)
            if len(xml.xpath("//value//text()")) == 0:
                print(f"<{model}>区域代码<{cc}>输入错误!!!")
            else:
                for node in xml.xpath("//value//text()"):
                    md5list.append(node)
                md5Dic[cc] = md5list
    return md5Dic


def char_to_number(char):
    '''
    字符转对应数字
    '''
    if char.isdigit():
            return int(char)
    elif char.isalpha() and char.isupper():
        return ord(char) - ord('A') + 10
    else:
        raise ValueError("输入必须是0-9或A-Z之间的字符")

def get_letters_range(start:str, end:str)->str:
    '''返回给定区间的字符串(包含end结束字符)'''
    # 获取A-Z的所有大写字母
    letters = "0123456789"+string.ascii_uppercase+string.ascii_lowercase
    # 找到起始和结束字母的索引位置
    start_index = letters.index(start)
    end_index = letters.index(end) + 1  # 加1是为了包含结束字母
    # 使用切片获取指定范围的字母
    if letters[start_index:end_index]=="":
        raise Exception("字符串开始和结束错误，请检查")
    else:
        return letters[start_index:end_index].upper()
def getFirmwareAddAndRemoveInfo(oldJson:list,newJson:list)->dict:
    '''
    获取固件版本号增删信息
    Args:
        oldJson(dict):保存旧版本号Md5的字典
        newJson(dict):保存新版本号Md5的字典
    Returns:
        dict:通过key:"added"获取新增固件版本;key:"removed"获取移除固件版本
    '''
    oldSet=set(oldJson)
    newSet=set(newJson)
    info={}
    info["added"]=newSet-oldSet
    info["removed"]=oldSet-newSet
    return info



def LoadOldMD5Firmware() -> dict:
    '''
    获取上次保存的固件版本号MD5信息
    Returns:
        历史MD5编码固件信息
    '''
    MD5VerFilePath = "MD5编码后的固件版本号.json"
    # 确保文件存在，如果不存在则创建并写入空字典
    if not os.path.isfile(MD5VerFilePath):
        with open(MD5VerFilePath, 'w', encoding='utf-8') as file:
            json.dump({}, file)
    
    # 从文件中加载JSON数据
    try:
        with open(MD5VerFilePath, 'r', encoding='utf-8') as file:
            oldFirmwareJson = json.load(file)
    except json.JSONDecodeError as e:
        # 如果文件内容不是有效的JSON，则返回空字典
        print(f"JSON解析错误,错误信息:{e}")
        oldFirmwareJson = {}
    
    return oldFirmwareJson
    

def UpdateOldFirmware(newDict:dict):
    '''
    更新历史固件版本号MD5信息
    Args:
        newDict(dict):新的MD5编码固件版本号
    '''
    global oldMD5Dict
    MD5VerFilePath="MD5编码后的固件版本号.json"
    with open(MD5VerFilePath, 'w', encoding='utf-8') as f:
        oldMD5Dict.update(newDict)
        f.write(json.dumps(oldMD5Dict, indent=4, ensure_ascii=False))

def WriteInfo(model:str,cc:str,AddAndRemoveInfo:dict):
    '''
    记录服务器固件变动信息
    Args:
        model(str):设备型号信息
        cc(str):设备地区代码
        AddAndRemoveInfo(str):包含增删固件版本信息
    '''
    global modelDic,isFirst
    MD5InfoFilePath="测试版固件变动信息.txt"
    if not os.path.exists(MD5InfoFilePath):
        with open(MD5InfoFilePath, 'w') as file:
            file.write('') 
    with open(MD5InfoFilePath, 'a+', encoding='utf-8') as f:
        if isFirst and len(AddAndRemoveInfo["added"])!=0 or len(AddAndRemoveInfo["removed"])!=0:
            f.write(f"*****记录时间:{getNowTime()}*****\n")
            isFirst = False
        for addVer in AddAndRemoveInfo["added"]:
            f.write(f"{modelDic[model]['name']}-{getCountryName(cc)}版服务器新增固件,对应版本号MD5编码为:<{addVer}>\n")
        for removeVer in AddAndRemoveInfo["removed"]:
            f.write(f"{modelDic[model]['name']}-{getCountryName(cc)}版服务器移除固件,对应版本号MD5编码为:<{removeVer}>\n")

def getNowTime()->str:
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
    return now

def get_next_char(char, alphabet="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    '''
    返回下一个字符
    '''
    if char in alphabet:
        index = alphabet.index(char)
        # 如果不是最后一个字符，返回下一个字符，否则返回第一个字符
        return alphabet[(index + 1) % len(alphabet)]
    else:
        raise ValueError(f"字符 '{char}' 不在给定的字符串中")

def get_pre_char(char, alphabet="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    '''
    返回上一个字符
    '''
    if char in alphabet:
        index = alphabet.index(char)
        # 如果不是第一个字符，返回上一个字符，否则返回最后字符
        return alphabet[(index -1) % len(alphabet)]
    else:
        raise ValueError(f"字符 '{char}' 不在给定的字符串中")

@func_set_timeout(2000)
def DecryptionFirmware(model:str, md5Dic:dict, cc:str)->dict:
    '''通过穷举解码固件号
    Args:
        model(str):设备型号
        md5Dic(dict):待解码固件版本号MD5字典
        cc(str):设备地区码
    Returns:
        dict:设备型号解码后的固件版本号字典
    '''
    print(f'开始解密<{model} {getCountryName(cc)}版>测试固件')
    md5list = md5Dic[cc]
    url=f"https://fota-cloud-dn.ospserver.net/firmware/{cc}/{model}/version.xml"
    content = requestXML(url)
    if content == None:
        return None
    try:
        xml = etree.fromstring(content)
        if(len(xml.xpath("//latest//text()"))==0):
            # 新设备初始化一个版本号
            ccList={"CHC":["ZC","CHC",3],"CHN":["ZC","CHC",2]} # CHC对应国行，后面的字典分别表示固件AP版本号、运营商CSC版本号前缀及是否带基带版本号，CHN为不带版本号
            if(cc in ccList.keys()):
                latestVer=""
                latestVerStr="暂无正式版"
                FirstCode=model.replace("SM-","")+ccList[cc][0]
                SecondCode=model.replace("SM-","")+ccList[cc][1]
                latestVer=""
                startYear =chr(datetime.now().year-2001+ord("A")) # 设置版本号默认开始年份，A代表2001年，设置从当前年份开始解密
                if(ccList[cc][2]>2):
                    # 初始化带基带固件版本号
                    ThirdCode=FirstCode
                else:
                    # 初始化不带基带固件版本号
                    ThirdCode=""
            else:
                print(f'设备<{model}>无<{cc}>初始化版本号信息，请手动添加后再试!')
                return
        else:
            # 直接获取服务器当前最新版本号信息
            latestVerStr = xml.xpath("//latest//text()")[0]  # 获取当前最新版本号数组
            latestVer = xml.xpath("//latest//text()")[0].split('/')  # 获取当前最新版本号数组
            FirstCode = latestVer[0][:-6]  # 如：N9860ZC
            SecondCode = latestVer[1][:-5]  # 如：N9860OZL
            ThirdCode = latestVer[2][:-6]  # 如：N9860OZC
            startYear=chr(datetime.now().year-2001-4+ord("A")) # 设置版本号默认开始年份，A代表2001年，设置从当前年份前3年开始解密
        Dicts = {} #新建一个字典
        Dicts[model] = {}
        Dicts[model][cc] = {}
        Dicts[model][cc]['版本号'] = {}
        Dicts[model][cc]['最新版本'] = ''
        DecDicts = {} #保存解码后的固件版本号
        oldDicts={} #缓存的固件版本号
        CpVersions = [] #以往的基带版本号
        VerFilePath = 'firmware.json'
        lastVersion = ''
        # 初始化开始
        startUpdateCount = "A"  # 设置版本号中更新次数为A，即第1次
        endUpdateCount="B"
        endYear=startYear 
        startBLVersion = "0" # 设置默认BL版本号为0
        endBLVersion="2"
        # 初始化结束
        with open(VerFilePath, 'r', encoding='utf-8') as f:
            # 导入旧数据，获取最后指定数量的基带版本号
            jsonStr = f.read()
            oldJson = {}
            if (jsonStr != ''):
                oldJson = json.loads(jsonStr)
            if model in oldJson.keys() and cc in oldJson[model].keys() and '最新测试版' in oldJson[model][cc].keys() and oldJson[model][cc]['最新测试版'] != '':
                lastVersion = oldJson[model][cc]['最新测试版'].split('/')[0]
                oldDicts=deepcopy(oldJson[model][cc]['版本号'])
                # CpVersions保存最近的3个基带版本
                seen = set()
                modelVersion=[x.split('/')[-1] for x in list(oldJson[model][cc]['版本号'].values())]
                newMV=[x for x in modelVersion if not (x in seen or seen.add(x))][-12:]#保存最近的12个基带版本
                CpVersions = newMV
        if (lastVersion != ''):
            startBLVersion = lastVersion[-5]
            if(lastVersion[-4]!='Z'):
                startUpdateCount = lastVersion[-4]
            else:
                startUpdateCount=latestVer[0][-4]
            startYear = lastVersion[-3]    #'A'表示2001年
        if(latestVer!=""):
            endBLVersion = get_next_char(latestVer[0][-5],"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") # 一直解密到当前bootloader版本+1，可能值为1
            endUpdateCount = get_next_char(latestVer[0][-4])   # 一直解密到当前大版本号+1
        updateLst = get_letters_range(startUpdateCount,endUpdateCount)
        updateLst+="Z" # 某些测试版倒数第4位以'Z'作为开头
        if(latestVer!=""):
            if(latestVer[0][-2] == "L"):
                endYear = get_next_char(latestVer[0][-3],"ABCDEFGHIJKLMNOPQRSTUVWXYZ")  #如果当前测试固件月份为12月，则将测试固件年份+1
            else:    
                endYear = latestVer[0][-3]  # 获取当前年份，,倒数第3位
        starttime = time.perf_counter()
        for i1 in "US":
            for bl_version in get_letters_range(startBLVersion, endBLVersion):  # 防止降级的版本
                for update_version in updateLst:
                    for yearStr in get_letters_range(startYear, endYear):
                        for monthStr in get_letters_range("A","L"):
                            tempCP=CpVersions[-12:].copy()
                            if ThirdCode!="":
                                for i in range(1,3):
                                    initCP = ThirdCode + i1 + bl_version+ update_version + yearStr+ monthStr +str(i) #手动指定当月基带版本
                                    tempCP.append(initCP)
                            for serialStr in ''.join(string.digits[1:] + string.ascii_uppercase):
                                # 添加基带使用未使用的AP版本号
                                initCP1 = ThirdCode + i1 + bl_version+ update_version + yearStr+ monthStr +get_pre_char(serialStr) 
                                initCP2 = ThirdCode + i1 + bl_version+ update_version + yearStr+ monthStr +get_pre_char(get_pre_char(serialStr))
                                tempCP.append(initCP1)
                                tempCP.append(initCP2)
                                randomVersion = bl_version+ update_version + yearStr+ monthStr+ serialStr  # 组合版本号
                                tempCode = ''if ThirdCode== '' else ThirdCode + i1 + randomVersion # Wifi版没有基带版本号
                                version1 = FirstCode + i1 + randomVersion + "/" + SecondCode + randomVersion + "/"+tempCode
                                if model in oldJson.keys() and cc in oldJson[model].keys() and '版本号' in oldJson[model][cc].keys() and version1 in oldJson[model][cc]['版本号'].values():
                                    continue
                                md5 = hashlib.md5()
                                md5.update(version1.encode(encoding="utf-8"))
                                # 基带和固件版本一致时
                                if md5.hexdigest() in md5list:
                                    DecDicts[md5.hexdigest()] = version1
                                    printStr(f'新增<{model} {getCountryName(cc)}版>测试固件:{version1}')
                                    if (version1.split('/')[2] != '') and  (version1.split('/')[2] not in CpVersions):
                                        CpVersions.append(
                                            version1.split('/')[2])
                                        tempCP.append(version1.split('/')[2])
                                # 固件更新，而基带未更新时
                                if (len(CpVersions) > 0):
                                    for tempCpVersion in tempCP:
                                        version2 = FirstCode + i1 + randomVersion + "/" + SecondCode + randomVersion + "/"+tempCpVersion
                                        if version1 == version2:
                                            continue
                                        if (model in oldJson.keys() and cc in oldJson[model].keys() and '版本号' in oldJson[model][cc].keys()) and version2 in oldJson[model][cc]['版本号'].values():
                                            continue
                                        md5 = hashlib.md5()
                                        md5.update(version2.encode(
                                            encoding="utf-8"))
                                        if md5.hexdigest() in md5list:
                                            DecDicts[md5.hexdigest()
                                                     ] = version2
                                            printStr(f'<基带>新增<{model} {getCountryName(cc)}版>测试固件:{version2}')         
                                            if (version2.split('/')[2] != '') and  (version2.split('/')[2] not in CpVersions):
                                                CpVersions.append(
                                                    version2.split('/')[2])
                                                tempCP.append(version2.split('/')[2])
                                # beta版以'Z'作为倒数第4位
                                vc2 = bl_version + 'Z' + \
                                    yearStr + monthStr+ serialStr  # 版本号
                                tempCode = ''if ThirdCode== ''  else ThirdCode + i1 + randomVersion
                                version3 = FirstCode + i1 + vc2 + "/" + SecondCode + vc2 + "/"+tempCode
                                if (model in oldJson.keys() and cc in oldJson[model].keys() and '版本号' in oldJson[model][cc].keys()) and version3 in oldJson[model][cc]['版本号'].values():
                                    continue
                                md5 = hashlib.md5()
                                md5.update(version3.encode(encoding="utf-8"))
                                if md5.hexdigest() in md5list:
                                    DecDicts[md5.hexdigest()] = version3
                                    if (version3.split('/')[2] != '') and  (version3.split('/')[2] not in CpVersions):
                                        CpVersions.append(
                                            version3.split('/')[2])
                                        tempCP.append(version3.split('/')[2])
                                if (len(CpVersions) > 0):
                                    for tempCpVersion in tempCP:
                                        version4 = FirstCode + i1 + vc2 + "/" + SecondCode + vc2 + "/"+tempCpVersion
                                        if version1 == version4:
                                            continue
                                        if (model in oldJson.keys() and cc in oldJson[model].keys() and '版本号' in oldJson[model][cc].keys()) and version4 in oldJson[model][cc]['版本号'].values():
                                            continue
                                        md5 = hashlib.md5()
                                        md5.update(version4.encode(
                                            encoding="utf-8"))
                                        if md5.hexdigest() in md5list:
                                            DecDicts[md5.hexdigest()
                                                     ] = version4
                                            printStr(f'<Z>新增<{model} {getCountryName(cc)}版>测试固件:{version4}')   
                                            if (version4.split('/')[2] != '') and  (version4.split('/')[2]  not in CpVersions):
                                                CpVersions.append(
                                                    version4.split('/')[2])
                                                tempCP.append(version4.split('/')[2])

        # 新增解密数据
        if len(DecDicts.values()) > 0:
            oldDicts.update(DecDicts)
            Dicts[model][cc]['最新测试版'] = sorted(
                oldDicts.values(), key=lambda x: x.split('/')[0][-3:])[-1]  # 记录最新版本号
            Dicts[model][cc]['版本号'] = DecDicts
        Dicts[model][cc]['最新正式版'] = latestVerStr
        endtime = time.perf_counter()
        # 如果有缓存数据
        if model in oldJson.keys() and cc in oldJson[model].keys() and '版本号' in oldJson[model][cc].keys() and len(oldJson[model][cc]["版本号"]) > 0:
            sumCount = len(Dicts[model][cc]["版本号"]) + \
                len(oldJson[model][cc]["版本号"])
            rateOfSuccess = round(sumCount/len(md5list)*100, 2)
        else:
            rateOfSuccess = round(
                len(Dicts[model][cc]["版本号"])/len(md5list)*100, 2)
        Dicts[model][cc]['解密百分比'] = f'{rateOfSuccess}%'
        print(
            f"<{modelDic[model]['name']} {getCountryName(cc)}版>测试版固件版本号解密完毕,耗时:{round(endtime - starttime, 2)}秒,解密成功百分比:{rateOfSuccess}%")
        if len(DecDicts) > 0:
            print(
                f"<{modelDic[model]['name']} {getCountryName(cc)}版>新增{len(DecDicts)}个测试版固件.")
        else:
            print(f"<{modelDic[model]['name']} {getCountryName(cc)}版>暂无新增测试版.")
        return Dicts
    except Exception as e:
        print(f'发生错误:{e}')


def telegram_bot(title: str, content: str) -> None:
    '''
    使用TG机器人推送消息
    '''
    if not push_config.get("TG_BOT_TOKEN") or not push_config.get("TG_USER_ID"):
        print("tg 服务的 bot_token 或者 user_id 未设置!!\n取消推送")
        return
    bot = telegram.Bot(token=push_config.get("TG_BOT_TOKEN"))
    message = bot.send_message(chat_id=push_config.get("TG_USER_ID"),
                               text=f"{title}\n\n{content}",
                               parse_mode=telegram.ParseMode.MARKDOWN,
                               disable_web_page_preview='true')
    if message.message_id > 0:
        print('TG消息发送成功!')


def fcm(title: str, content='', link='') -> None:
    '''
    使用FCM推送消息
    '''
    if link == '':
        data2 = {"title": title, "message": content}
        data1 = {"text": data2}
    else:
        data2 = {"title": title, "url": link}
        data1 = {"link": data2}

    data = {"to": push_config.get(
        "FCM_KEY"), "time_to_live": 60, "priority": "high", "data": data1}
    headers = {
        'authorization': f'key={push_config.get("FCM_API_KEY")}', 'Content-Type': 'application/json'}
    url = "https://fcm.googleapis.com/fcm/send"
    response = requests.post(url=url, data=json.dumps(data), headers=headers)
    if response.status_code == 200:
        print('FCM发送成功')


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



def run():
    # 获取相关参数变量数据
    for k in push_config:
        if os.getenv(k):
            v = os.getenv(k)
            push_config[k] = v
    global modelDic,oldMD5Dict
    jsonStr = ""
    decDicts = {"上次更新时间": getNowTime()}
    VerFilePath = 'firmware.json'
    AddTxtPath = '测试版新增日志.txt'
    startTime = time.perf_counter()
    if not os.path.exists(VerFilePath):
        with open(VerFilePath, 'w') as file:
            file.write('{}') 
    with open(VerFilePath, 'r', encoding='utf-8') as f:
        jsonStr = f.read()
        oldJson = {}
        if (jsonStr != ''):
            oldJson = json.loads(jsonStr)
        hasNewVersion = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            tasks = []
            for model in modelDic:
                # 开启多进程解密
                task = pool.submit(
                    getNewVersions, decDicts, oldJson, model)
                tasks.append(task)
            for task in concurrent.futures.as_completed(tasks):
                if task.result() != None:
                    hasNew, newMDic = task.result()
                    if (hasNew):
                        hasNewVersion = True
                    decDicts.update(newMDic)
        if hasNewVersion:
            # 固件更新日志
            with open(AddTxtPath, 'a+', encoding='utf-8') as file:
                isFirst = True
                for model in modelDic:
                    if (model in decDicts) and (model in oldJson):
                        for cc in modelDic[model]['CC']:
                            if not cc in oldJson[model] or not cc in decDicts[model]:
                                continue
                            md5Keys = decDicts[model][cc]['版本号'].keys(
                            ) - oldJson[model][cc]['版本号'].keys()  # 获取新增的版本号
                            if len(md5Keys) > 0:
                                if isFirst:
                                    file.write(f"*****记录时间:{getNowTime()}*****\n")
                                    isFirst = False
                                Str = ''
                                newVersions = {}
                                for md5key in md5Keys:
                                    newVersions[md5key] = decDicts[model][cc]['版本号'][md5key]
                                newVersions = dict(
                                    sorted(newVersions.items(), key=lambda x: x[1]))  # 按照版本号排序
                                for key, value in newVersions.items():
                                    textStr = "\n"+value
                                    Str += f"{modelDic[model]['name']}-{getCountryName(cc)}版新增测试固件版本:{value}，对应MD5值：{key}\n"
                                file.write(Str)
                                fcm(f"{modelDic[model]['name']}——{getCountryName(cc)}新增内测固件",
                                    content=textStr.replace('*', ''))
                                telegram_bot(
                                    f"#{modelDic[model]['name']}——{getCountryName(cc)}新增内测固件", textStr)
            # 更新全机型最新版
            with open('各机型最新版本.md', 'w', encoding='utf-8') as f:
                textStr = ''
                for model in modelDic.keys():
                    for cc in modelDic[model]['CC']:
                        if not cc in decDicts[model].keys():
                            continue
                        textStr += f"*{modelDic[model]['name']} {getCountryName(cc)}版：*\n{decDicts[model][cc]['最新测试版']}\n\n"
                f.write(textStr)
                # fcm("各机型最新测试版", content=textStr.replace('*', ''))
                # telegram_bot("#各机型最新测试版", textStr)
    endTime = time.perf_counter()
    print(f'总耗时:{round(endTime - startTime, 2)}秒')
    with open(VerFilePath, 'w', encoding='utf-8') as f:
        f.write(json.dumps(decDicts, indent=4, ensure_ascii=False))


def getNewVersions(decDicts, oldJson, model):
    global oldMD5Dict
    md5Dic = readXML(model)  # 返回包含多个地区版本的md5字典
    if len(md5Dic) == 0:
        return
    newMDic = {}    #保存解密后的固件版本号信息
    newMDic[model] = {}
    newMD5Dict={"上次更新时间": getNowTime()}   #最新的MD5编码固件版本号信息
    newMD5Dict[model]={}
    hasNewVersion = False
    for cc in md5Dic.keys():
        if model in oldJson.keys() and cc in oldJson[model].keys():
            # 拷贝已有设备固件版本内容
            newMDic[model][cc] = deepcopy(oldJson[model][cc])
        else:
            # 新增设备初始化内容
            newMDic[model][cc] = {}
            newMDic[model][cc]['版本号'] = {}
            newMDic[model][cc]['最新测试版'] = ''
            newMDic[model][cc]['最新正式版'] = ''
            newMDic[model][cc]['最新版本号说明'] = ''
            newMDic[model][cc]['解密百分比'] = ''
        decDicts.update(newMDic)  # 先保存已有的数据
        if model in oldMD5Dict.keys() and cc in oldMD5Dict[model].keys():
            # 获取MD5编码的固件版本号增减信息
            newMD5Dict[model][cc]=deepcopy(oldMD5Dict[model][cc])
            oldMD5Vers=oldMD5Dict[model][cc]["版本号"]
            newMD5Vers=md5Dic[cc]
            addAndRemoveInfo=getFirmwareAddAndRemoveInfo(oldJson=oldMD5Vers,newJson=newMD5Vers)
            WriteInfo(model=model,cc=cc,AddAndRemoveInfo=addAndRemoveInfo)
        else:
            # 新增设备初始化内容
            newMD5Dict[model]={}
            newMD5Dict[model][cc]={}
            newMD5Dict[model][cc]['版本号']={}
            newMD5Dict[model][cc]["固件数量"]=0
        newMD5Dict[model][cc]["版本号"]=md5Dic[cc]
        newMD5Dict[model][cc]["固件数量"]=len(md5Dic[cc])
        verDic = DecryptionFirmware(model, md5Dic, cc)  # 解密获取新数据
        if newMDic[model][cc]['最新正式版'] != '' and verDic != None and verDic[model][cc]['最新正式版'] != newMDic[model][cc]['最新正式版']:
            # 正式版更新时发送通知
            telegram_bot(f"#{model} - {newMDic[model][cc]['机型']} {getCountryName(cc)}版推送更新",
                         f"版本:{verDic[model][cc]['最新正式版']}\n[查看更新日志](https://doc.samsungmobile.com/{model}/{cc}/doc.html)")
            fcm(f"#{model} {getCountryName(cc)}版推送更新,版本:{verDic[model][cc]['最新正式版']}",
                link=f"https://doc.samsungmobile.com/{model}/{cc}/doc.html")
            newMDic[model][cc]['最新正式版'] = verDic[model][cc]['最新正式版']
        newMDic[model][cc]['地区'] = getCountryName(cc)
        newMDic[model][cc]['机型'] = modelDic[model]['name']
        if verDic==None or len(verDic[model][cc]['版本号']) == 0:
            continue
        diffModel = []
        if verDic[model][cc]['最新测试版'] != '':
            newMDic[model][cc]['最新测试版'] = verDic[model][cc]['最新测试版']
        if verDic[model][cc]['最新正式版'] != '':
            newMDic[model][cc]['最新正式版'] = verDic[model][cc]['最新正式版']
        ver = newMDic[model][cc]['最新测试版'].split('/')[0]
        yearStr = ord(ver[-3])-65+2001  # 获取更新年份
        monthStr = ord(ver[-2])-64  # 获取更新月份
        countStr = char_to_number(ver[-1])   # 获取第几次更新
        definitionStr = f'{yearStr}年{monthStr}月第{countStr}个测试版'
        newMDic[model][cc]['最新版本号说明'] = definitionStr
        if verDic[model][cc]['解密百分比'] != '':
            newMDic[model][cc]['解密百分比'] = verDic[model][cc]['解密百分比']
            # 如果有缓存数据，则获取差集
        if len(newMDic[model][cc]['版本号'].keys()) > 0:
            diffModel = verDic[model][cc]['版本号'].keys(
            )-newMDic[model][cc]['版本号'].keys()
        else:
            diffModel = verDic[model][cc]['版本号'].keys()
        if len(diffModel) > 0:
            hasNewVersion = True
            for key in diffModel:
                # 存入新的版本号
                newMDic[model][cc]['版本号'][key] = verDic[model][cc]['版本号'][key]
        newMDic[model][cc]['版本号'] = dict(
            sorted(newMDic[model][cc]['版本号'].items(), key=lambda x: x[1].split('/')[0][-3:]))
        newMDic[model][cc]['解密数量'] = len(newMDic[model][cc]['版本号'])
    UpdateOldFirmware(newMD5Dict)   #更新历史固件Json信息
    return hasNewVersion, newMDic


if __name__ == '__main__':
    try:
        isDebug=False
        isFirst=True
        oldMD5Dict=LoadOldMD5Firmware() #获取上次的MD5编码版本号数据
        if isDebug:
            modelDic=dict(list(getModelDictsFromDB().items())[:5])  #测试时使用
        else:
            modelDic = getModelDictsFromDB()  # 获取型号信息
        run()
    except func_timeout.exceptions.FunctionTimedOut:
        print('任务超时，已退出执行!')
