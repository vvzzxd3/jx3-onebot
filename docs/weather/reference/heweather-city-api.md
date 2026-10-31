# 城市搜索 API 文档

## 概述

城市搜索API提供全球地理位位置、全球城市搜索服务，支持经纬度坐标反查、多语言、模糊搜索等功能。

天气数据是基于地理位置的数据，因此获取天气之前需要先知道具体的位置信息。使用城市搜索，可获取到该城市的基本信息，包括城市的Location ID（你需要这个ID去查询天气），多语言名称、经纬度、时区、海拔、Rank值、归属上级行政区域、所在行政区域等。

另外，城市搜索也可以帮助你在你的APP中实现模糊搜索，用户只需要输入1-2个字即可获得结果。

---

## 接口信息

### 请求路径
```
GET /geo/v2/city/lookup
```

### 请求参数

| 参数名 | 是否必选 | 类型 | 描述 |
|--------|----------|------|------|
| location | 是 | string | 需要查询地区的名称、LocationID或以英文逗号分隔的经度,纬度坐标（十进制），LocationID可通过GeoAPI获取。例如 `location=101010100` 或 `location=116.41,39.92` |
| adm | 否 | string | 城市的上级行政区划，可设定只在某个行政区划范围内进行搜索，用于排除重名城市或对结果进行过滤。例如 `adm=beijing` |
| range | 否 | string | 搜索范围，可设定只在某个国家或地区范围内进行搜索，国家和地区名称需使用ISO 3166 所定义的国家代码。如果不设置此参数，搜索范围将在所有城市。例如 `range=cn` |
| number | 否 | integer | 返回结果的数量，取值范围1-20，默认返回10个结果 |
| lang | 否 | string | 语言代码（具体支持语言请参考官方文档） |

---

## 请求示例

```bash
curl -X GET --compressed \
-H 'Authorization: Bearer your_token' \
'https://your-api-host/geo/v2/city/lookup?location=116.41%2C39.92'
```

> **注意**：请将 `your_token` 替换为你的JWT身份认证，将 `your_api_host` 替换为你的API Host。

---

## 返回数据

### 响应结构示例

```json
{
  "code": "200",
  "location": [
    {
      "name": "东城",
      "id": "101011600",
      "lat": "39.91755",
      "lon": "116.41876",
      "adm2": "北京",
      "adm1": "北京市",
      "country": "中国",
      "tz": "Asia/Shanghai",
      "utcOffset": "+08:00",
      "isDst": "0",
      "type": "city",
      "rank": "35",
      "fxLink": "https://www.qweather.com/weather/dongcheng-101011600.html"
    }
  ],
  "refer": {
    "sources": [
      "https://developer.qweather.com/attribution.html"
    ],
    "license": [
      "QWeather Developers License"
    ]
  }
}
```

### 返回字段说明

#### 根节点

| 字段名 | 类型 | 描述 |
|--------|------|------|
| code | string | 状态码，`"200"` 表示成功 |
| location | array | 地区/城市信息列表 |
| refer | object | 数据来源和许可信息 |

#### location 数组元素

| 字段名 | 类型 | 描述 |
|--------|------|------|
| name | string | 位置名称 |
| id | string | 位置ID（用于查询天气） |
| lat | string | 纬度 |
| lon | string | 经度 |
| adm2 | string | 上级行政区划名称 |
| adm1 | string | 一级行政区域名称 |
| country | string | 国家名称 |
| tz | string | 时区名称 |
| utcOffset | string | UTC偏移时间 |
| isDst | string | 是否处于夏令时。`"1"` 表示当前处于夏令时，`"0"` 表示当前不是夏令时 |
| type | string | 位置的属性（如 city, district 等） |
| rank | string | 排序权重（数值越高表示城市越重要） |
| fxLink | uri | 该位置的天气预报网页链接，便于嵌入你的网站或应用 |

#### refer 对象

| 字段名 | 类型 | 描述 |
|--------|------|------|
| sources | array | 原始数据来源，或数据源说明，可能为空 |
| license | array | 数据许可或版权声明，可能为空 |

---

## 使用说明

1. **获取天气的前提**：必须先通过此API获取城市的 `id`（Location ID），再使用该ID查询天气数据。
2. **模糊搜索支持**：用户只需输入1-2个字符即可获得匹配的城市列表。
3. **坐标反查**：支持传入经纬度坐标，返回该坐标对应的地理位置信息。
4. **多语言支持**：通过 `lang` 参数可获取不同语言的结果（具体支持语言请查阅官方文档）。

---

> 本文档根据 [和风天气开发平台 - 城市搜索 API](https://dev.qweather.com/docs/api/geoapi/city-lookup/) 整理。