# 每日天气预报 API 文档

## 概述

获取指定经纬度位置的每日天气预报，最多10天预报，1公里分辨率，覆盖全球任意地点。

每日预报提供白天 [07:00, 19:00)、晚间 [19:00, 次日07:00) 的预报数据，包括：最高和最低温度、天气现象、最大阵风、最大紫外线指数、降水量和概率、海平面气压、湿度、风向和风速、云量、日出日落、月升月落和月相等。

---

## 接口信息

### 请求路径
```
GET /weather/v1/daily/{latitude}/{longitude}
```

### 请求参数

#### 路径参数

| 参数名 | 是否必选 | 类型 | 描述 |
|--------|----------|------|------|
| latitude | 是 | number | 所需位置的纬度。十进制，最多支持小数点后两位。例如 `39.92` |
| longitude | 是 | number | 所需位置的经度。十进制，最多支持小数点后两位。例如 `116.41` |

#### 查询参数

| 参数名 | 是否必选 | 类型 | 描述 |
|--------|----------|------|------|
| days | 否 | integer | 预报天数，支持1-10天，默认返回7天 |
| localTime | 否 | boolean | 是否返回查询地点的本地时间。可选值：`false`（UTC时间，默认），`true`（本地时间） |
| lang | 否 | string | 语言代码（具体支持语言请参考官方文档） |

---

## 请求示例

```bash
curl -X GET --compressed \
-H 'Authorization: Bearer your_token' \
'https://your-api-host/weather/v1/daily/39.92/116.41'
```

> **注意**：请将 `your_token` 替换为你的JWT身份认证，将 `your_api_host` 替换为你的API Host。

---

## 返回数据

### 响应结构示例

```json
{
  "metadata": {
    "tag": "dabb37d79f0ba4498d5b92c873d33c862921eb1cb688181b84b3948306ea66bc",
    "attributions": [
      "https://developer.qweather.com/attribution.html"
    ]
  },
  "days": [
    {
      "forecastStartTime": "2024-08-10T22:00Z",
      "forecastEndTime": "2024-08-11T22:00Z",
      "astro": {
        "sunrise": "2024-08-11T04:22Z",
        "sunset": "2024-08-11T19:34Z",
        "astronomicalDawn": "2024-08-11T01:52Z",
        "nauticalDawn": "2024-08-11T02:54Z",
        "civilDawn": "2024-08-11T03:44Z",
        "astronomicalDusk": "2024-08-11T22:04Z",
        "nauticalDusk": "2024-08-11T21:01Z",
        "civilDusk": "2024-08-11T20:12Z",
        "solarNoon": "2024-08-11T11:58Z",
        "solarMidnight": "2024-08-10T23:58Z",
        "moonrise": "2024-08-11T20:00Z",
        "moonset": "2024-08-11T04:57Z",
        "moonTransit": "2024-08-11T00:19Z",
        "moonUnderfoot": "2024-08-11T12:19Z",
        "moonPhase": "waning-gibbous"
      },
      "temperatureMax": {
        "value": 29.94,
        "unit": "°C"
      },
      "temperatureMin": {
        "value": 20.93,
        "unit": "°C"
      },
      "temperatureAvg": {
        "value": 25.57,
        "unit": "°C"
      },
      "uvIndexMax": 6,
      "daytime": {
        "forecastStartTime": "2024-08-11T05:00Z",
        "forecastEndTime": "2024-08-11T17:00Z",
        "condition": {
          "text": "小雨",
          "code": "305"
        },
        "temperatureMax": {
          "value": 29.94,
          "unit": "°C"
        },
        "temperatureMin": {
          "value": 20.93,
          "unit": "°C"
        },
        "wind": {
          "direction": {
            "degree": 270,
            "compass": "w"
          },
          "speed": {
            "value": 1.87,
            "unit": "m/s"
          },
          "scale": 2
        },
        "windGustMax": {
          "value": 6.64,
          "unit": "m/s"
        },
        "precipitation": {
          "amount": {
            "value": 0.75,
            "unit": "mm"
          },
          "probability": 0.64,
          "type": "rain"
        },
        "cloudCover": 0.32,
        "humidity": 0.52
      },
      "nighttime": {
        "forecastStartTime": "2024-08-11T17:00Z",
        "forecastEndTime": "2024-08-12T05:00Z",
        "condition": {
          "text": "晴间多云",
          "code": "103"
        },
        "temperatureMax": {
          "value": 29.8,
          "unit": "°C"
        },
        "temperatureMin": {
          "value": 19.95,
          "unit": "°C"
        },
        "wind": {
          "direction": {
            "degree": 343,
            "compass": "nnw"
          },
          "speed": {
            "value": 2.33,
            "unit": "m/s"
          },
          "scale": 2
        },
        "windGustMax": {
          "value": 7.16,
          "unit": "m/s"
        },
        "precipitation": {
          "amount": {
            "value": 0,
            "unit": "mm"
          },
          "probability": 0,
          "type": "none"
        },
        "cloudCover": 0.34,
        "humidity": 0.56
      }
    }
    // ... 更多天数的数据
  ]
}
```

### 返回字段说明

#### 根节点

| 字段名 | 类型 | 描述 |
|--------|------|------|
| metadata | object | 元数据信息 |
| days | array | 每日天气预报列表 |

#### metadata 对象

| 字段名 | 类型 | 描述 |
|--------|------|------|
| tag | string | 数据唯一标识 |
| attributions | array | 数据归因信息或声明，必须与当前数据共同显示 |

#### days 数组元素

| 字段名 | 类型 | 描述 |
|--------|------|------|
| forecastStartTime | date-time | 预报数据的开始时间 |
| forecastEndTime | date-time | 预报数据的结束时间（不含） |
| astro | object | 太阳和月亮天文事件 |
| temperatureMax | object | 预报时段的最高温度 |
| temperatureMin | object | 预报时段的最低温度 |
| temperatureAvg | object | 当日平均温度 |
| uvIndexMax | number | 当日最大紫外线指数，取值范围 [0, 15] |
| daytime | object | 白天预报，当地时间 [07:00, 19:00) |
| nighttime | object | 夜间预报，当地时间 [19:00, 次日07:00) |

#### astro 对象

| 字段名 | 类型 | 描述 |
|--------|------|------|
| sunrise | date-time | 日出时间 |
| sunset | date-time | 日落时间 |
| astronomicalDawn | date-time | 天文晨光的开始时间 |
| nauticalDawn | date-time | 航海晨光的开始时间 |
| civilDawn | date-time | 民用晨光的开始时间 |
| astronomicalDusk | date-time | 天文暮光的结束时间 |
| nauticalDusk | date-time | 航海暮光的结束时间 |
| civilDusk | date-time | 民用暮光的结束时间 |
| solarNoon | date-time | 太阳正午时间 |
| solarMidnight | date-time | 太阳子夜时间 |
| moonrise | date-time | 月出时间 |
| moonset | date-time | 月落时间 |
| moonTransit | date-time | 月亮上中天时间 |
| moonUnderfoot | date-time | 月亮下中天时间 |
| moonPhase | string | 当天主导月相，可选值：`new-moon`（新月）、`waxing-crescent`（蛾眉月）、`first-quarter`（上弦月）、`waxing-gibbous`（盈凸月）、`full-moon`（满月）、`waning-gibbous`（亏凸月）、`last-quarter`（下弦月）、`waning-crescent`（残月） |

#### temperatureMax / temperatureMin / temperatureAvg 对象

| 字段名 | 类型 | 描述 |
|--------|------|------|
| value | number | 温度数值 |
| unit | string | 温度单位（如 `°C`） |

#### daytime / nighttime 对象

| 字段名 | 类型 | 描述 |
|--------|------|------|
| forecastStartTime | date-time | 预报数据的开始时间 |
| forecastEndTime | date-time | 预报数据的结束时间（不含） |
| condition | object | 天气现象信息 |
| temperatureMax | object | 预报时段的最高温度（含 value 和 unit） |
| temperatureMin | object | 预报时段的最低温度（含 value 和 unit） |
| humidity | number | 预报时段的平均相对湿度，取值范围 [0, 1] |
| wind | object | 风的数据 |
| windGustMax | object | 预报时段的最大阵风风速（含 value 和 unit） |
| precipitation | object | 降水数据 |
| cloudCover | number | 预报时段的平均总云量，取值范围 [0, 1] |

##### condition 对象

| 字段名 | 类型 | 描述 |
|--------|------|------|
| text | string | 天气现象的本地化描述 |
| code | string | 天气现象代码 |

##### wind 对象

| 字段名 | 类型 | 描述 |
|--------|------|------|
| direction | object | 风向数据（含 degree 和 compass） |
| speed | object | 风速数据（含 value 和 unit） |
| scale | number | 风力等级 |

**direction.compass 可选值**：
`n`, `nne`, `ne`, `ene`, `e`, `ese`, `se`, `sse`, `s`, `ssw`, `sw`, `wsw`, `w`, `wnw`, `nw`, `nnw`, `none`, `vrb`

##### precipitation 对象

| 字段名 | 类型 | 描述 |
|--------|------|------|
| amount | object | 当前数据时段内的累计降水量（含 value 和 unit） |
| probability | number | 降水概率，取值范围 [0, 1] |
| type | string | 降水类型代码：`rain`（雨）、`snow`（雪）、`ice`（冰粒或冻雨）、`mixed`（混合降水）、`none`（无降水）、`unknown`（未知） |

---

## 使用说明

1. **获取天气数据的前提**：需先通过城市搜索API或其他方式获取目标地点的经纬度坐标。
2. **预报天数**：可通过 `days` 参数灵活控制返回的预报天数（1-10天），默认7天。
3. **时间格式**：默认返回UTC时间，可通过 `localTime=true` 获取查询地点的本地时间。
4. **精细预报**：每天分为白天和夜间两个时段，提供更精细的天气信息。

---

> 本文档根据 [和风天气开发平台 - 每日天气预报 API](https://dev.qweather.com/docs/api/weather/weather-daily-forecast/) 整理。