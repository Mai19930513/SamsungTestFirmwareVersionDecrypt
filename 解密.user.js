// ==UserScript==
// @name        解密三星加密固件版本号
// @namespace   Violentmonkey Scripts
// @match       https://fota-cloud-dn.ospserver.net/firmware/*/version.test.xml
// @grant       none
// @version     1.1
// @author      Mai
// @description 2022/10/10
// ==/UserScript==
(function decryptVersion() {
    var httpRequest = new XMLHttpRequest();
    var url = 'https://raw.githubusercontent.com/Mai19930513/SamsungTestFirmwareVersionDecrypt/master/firmware.json'
    httpRequest.open('GET', url, true);
    httpRequest.send();
    httpRequest.onreadystatechange = function () {
        if (httpRequest.readyState == 4 && httpRequest.status == 200) {
            var jsonStr = httpRequest.responseText;
            var jsObj = JSON.parse(jsonStr);
            var md5List = [];
            var doc = document.querySelectorAll("#folder3>div.opened>div");
            var md5Count = doc.length;
            for (var i = 0; i < md5Count; i++) {
                md5List.push(doc[i].childNodes[1].innerHTML);
            }
            var host = window.location.href.split("/")
            var model = host[host.length - 2]
            if (jsObj[model] != null) {
                for (var i in md5List) {
                    if (jsObj[model]['versions'][md5List[i]] != null) {
                        doc[i].childNodes[1].innerHTML = jsObj[model]['versions'][md5List[i]];
                        doc[i].childNodes[1].setAttribute("style", "color:#FF0000")
                    }
                }
            }
            sortElement(doc)
        }
    };

    function sortElement(lst) {
        var domArray = Array.prototype.slice.call(lst, 0);
        var arr = [];
        var parNode = lst[0].parentNode;
        for (var i = 0; i < domArray.length; i++) {
            arr[i] = domArray[i];
        }
        arr.sort(function (a, b) {
            aText = a.childNodes[1].innerHTML;
            bText = b.childNodes[1].innerHTML
            if (aText > bText) return 1;
            else if (aText < bText) return -1;
            else return 0;
        })
        for (var i = 0; i < arr.length; i++) {
            parNode.appendChild(arr[i])
        }
    }
}
)();
