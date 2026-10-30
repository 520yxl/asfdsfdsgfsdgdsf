"""Shared country/region, language and script taxonomy for MoxYi font catalog."""
import re

LANGUAGE_NAMES = {
    "zh-Hans": "简体中文", "zh-Hant": "繁体中文", "en": "英语", "ja": "日语",
    "ko": "韩语", "de": "德语", "fr": "法语", "ru": "俄语", "ar": "阿拉伯语",
    "hi": "印地语", "th": "泰语", "vi": "越南语", "es": "西班牙语",
    "pt": "葡萄牙语", "it": "意大利语", "nl": "荷兰语", "pl": "波兰语",
    "tr": "土耳其语", "uk": "乌克兰语", "he": "希伯来语", "bn": "孟加拉语",
    "ta": "泰米尔语", "te": "泰卢固语", "fa": "波斯语", "el": "希腊语",
    "sv": "瑞典语", "no": "挪威语", "da": "丹麦语", "fi": "芬兰语",
    "cs": "捷克语", "hu": "匈牙利语", "id": "印度尼西亚语", "ms": "马来语",
}
REGION_NAMES = {
    "CN": "中国大陆", "TW": "中国台湾", "HK": "中国香港", "MO": "中国澳门",
    "JP": "日本", "KR": "韩国", "US": "美国", "GB": "英国", "DE": "德国",
    "FR": "法国", "RU": "俄罗斯", "IN": "印度", "TH": "泰国", "VN": "越南",
    "IT": "意大利", "ES": "西班牙", "BR": "巴西", "CA": "加拿大",
    "AU": "澳大利亚", "NL": "荷兰", "PL": "波兰", "CZ": "捷克",
}
SCRIPT_NAMES = {
    "Han": "汉字", "Latin": "拉丁字母", "Cyrillic": "西里尔字母",
    "Arabic": "阿拉伯字母", "Hebrew": "希伯来字母", "Hangul": "韩文",
    "Hiragana-Katakana": "日文假名", "Devanagari": "天城文",
    "Thai": "泰文", "Greek": "希腊字母", "Bengali": "孟加拉文",
    "Tamil": "泰米尔文", "Telugu": "泰卢固文",
}
LANGUAGE_ALIASES = {
    "zh": "zh-Hans", "zho": "zh-Hans", "chi": "zh-Hans", "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans", "zh-hans": "zh-Hans", "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant", "zh-mo": "zh-Hant", "zh-hant": "zh-Hant",
    "en-us": "en", "en-gb": "en", "eng": "en", "jpn": "ja", "kor": "ko",
    "deu": "de", "ger": "de", "fra": "fr", "fre": "fr", "rus": "ru",
    "ara": "ar", "hin": "hi", "tha": "th", "vie": "vi",
}
REGION_ALIASES = {
    "china": "CN", "mainland china": "CN", "cn": "CN",
    "taiwan": "TW", "tw": "TW", "hong kong": "HK", "hk": "HK",
    "macau": "MO", "macao": "MO", "mo": "MO", "japan": "JP", "jp": "JP",
    "korea": "KR", "south korea": "KR", "kr": "KR", "united states": "US",
    "usa": "US", "us": "US", "united kingdom": "GB", "uk": "GB", "gb": "GB",
    "germany": "DE", "de": "DE", "france": "FR", "fr": "FR",
    "russia": "RU", "ru": "RU", "india": "IN", "in": "IN",
    "thailand": "TH", "th": "TH", "vietnam": "VN", "vn": "VN",
}

def normalize_languages(values):
    result = set()
    for value in values or []:
        raw = str(value).strip().replace("_", "-")
        low = raw.casefold()
        tag = LANGUAGE_ALIASES.get(low)
        if not tag:
            primary = low.split("-", 1)[0]
            tag = LANGUAGE_ALIASES.get(primary, primary if re.fullmatch(r"[a-z]{2,3}", primary) else "")
        if tag:
            result.add(tag)
    return sorted(result)

def normalize_regions(values):
    result = set()
    for value in values or []:
        key = str(value).strip().casefold()
        code = REGION_ALIASES.get(key, str(value).upper() if re.fullmatch(r"[A-Za-z]{2}", str(value).strip()) else "")
        if code in REGION_NAMES:
            result.add(code)
    return sorted(result)

def enrich(record, languages=None, regions=None, scripts=None):
    langs = normalize_languages(languages if languages is not None else record.get("language_tags", []))
    regs = normalize_regions(regions if regions is not None else record.get("region_tags", []))
    scrs = sorted(set(scripts if scripts is not None else record.get("script_tags", [])))
    record["region_tags"] = regs
    record["region_names"] = [REGION_NAMES[x] for x in regs]
    record["language_tags"] = langs
    record["language_names"] = [LANGUAGE_NAMES.get(x, x) for x in langs]
    record["script_tags"] = scrs
    record["script_names"] = [SCRIPT_NAMES.get(x, x) for x in scrs]
    record["region_source"] = record.get("region_source", "verified" if regs else "unknown")
    return record

def filter_indexes(fonts):
    def counts(key, names):
        result = {}
        for font in fonts:
            for code in font.get(key, []):
                entry = result.setdefault(code, {"code": code, "name": names.get(code, code), "count": 0})
                entry["count"] += 1
        return sorted(result.values(), key=lambda x: (x["name"], x["code"]))
    return {
        "regions": counts("region_tags", REGION_NAMES),
        "languages": counts("language_tags", LANGUAGE_NAMES),
        "scripts": counts("script_tags", SCRIPT_NAMES),
    }
