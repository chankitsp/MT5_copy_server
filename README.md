# MT5 Copy Server

โปรเจกต์นี้เป็นระบบ copy trade แบบง่ายสำหรับส่งคำสั่งจาก API server ไปยัง MT5 follower หลายเครื่อง

- `server.py` รับ event ผ่าน REST API และ push event ไปยัง follower ผ่าน WebSocket
- `follower.py` เชื่อม WebSocket ค้างไว้ รับ event แบบ realtime แล้วส่งคำสั่งเข้า MetaTrader 5
- รองรับหลาย follower ด้วย `follower_id` แยกตามแต่ละเครื่อง

## โครงสร้างไฟล์

- `server.py` FastAPI server
- `follower.py` MT5 follower client
- `requirements.txt` รายการ Python libraries ที่ต้องติดตั้ง
- `follower_state.json` state ของ follower สำหรับกัน event ซ้ำ

## การทำงานโดยย่อ

1. ส่ง event ไปที่ `POST /events`
2. server push event ไปยัง follower ที่เชื่อม WebSocket อยู่
3. follower เปิดหรือปิด order ใน MT5
4. follower ส่ง `ack` กลับมาทาง WebSocket
5. admin ตรวจสอบสถานะได้ผ่าน REST API

## ข้อกำหนด

- Python 3.14 หรือใกล้เคียง
- MetaTrader 5 Terminal

ติดตั้ง dependencies:

```powershell
python -m pip install -r requirements.txt
```

ไฟล์ [requirements.txt](/D:/Coding/MT5_copy_server/requirements.txt) มีรายการดังนี้:

```txt
fastapi
uvicorn
pydantic
websockets
MetaTrader5
```

## ตั้งค่า

แก้ค่าคงที่ในไฟล์ตามเครื่องของคุณ

ใน `server.py`

- `API_TOKEN`

ใน `follower.py`

- `API_BASE`
- `API_TOKEN`
- `LOT_MULTIPLIER`
- `SYMBOL_MAP`

ถ้าต้องการกำหนด `follower_id` เอง:

```powershell
$env:FOLLOWER_ID="mt5-01"
python follower.py
```

ถ้าไม่กำหนด ระบบจะใช้ชื่อเครื่องจาก `hostname`

## รัน Server

จากโฟลเดอร์โปรเจกต์:

```powershell
python -m uvicorn server:app --host 0.0.0.0 --port 5990
```

ตรวจสอบได้ที่:

- `http://127.0.0.1:5990/`
- `http://127.0.0.1:5990/status`

## รัน Follower

```powershell
python follower.py
```

เมื่อเริ่มรัน follower จะ:

- ต่อกับ MT5
- จำเวลาเริ่มต้นของโปรแกรม
- เชื่อม WebSocket ไปที่ server
- ไม่เปิด `open` event ที่เก่ากว่าเวลาเริ่มรัน
- reconnect อัตโนมัติเมื่อ connection หลุด

## API

ทุก REST request ต้องส่ง header:

```text
Authorization: Bearer change-me
```

## REST API

### `POST /events`

ใช้ส่ง event เข้า queue และ push ต่อไปยัง follower ที่ออนไลน์อยู่

payload สำหรับเปิด order:

```json
{
  "event_id": "open-15072",
  "action": "open",
  "symbol": "BTCUSD",
  "side": "sell",
  "volume": 1,
  "magic": 0,
  "timestamp": 1774677300,
  "sl": 0.0,
  "tp": 0.0
}
```

payload สำหรับปิด order:

```json
{
  "event_id": "close-15072",
  "action": "close",
  "symbol": "BTCUSD",
  "side": "sell",
  "volume": 1,
  "magic": 0,
  "timestamp": 1774677360,
  "sl": 0.0,
  "tp": 0.0
}
```

ตัวอย่าง PowerShell:

```powershell
$headers = @{ Authorization = "Bearer change-me" }
$body = @{
  event_id = "open-15072"
  action = "open"
  symbol = "BTCUSD"
  side = "sell"
  volume = 1
  magic = 0
  timestamp = 1774677300
  sl = 0.0
  tp = 0.0
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5990/events" -Headers $headers -Body $body -ContentType "application/json"
```

### `GET /status`

ใช้ดูภาพรวมของระบบ เช่น:

- จำนวน event ทั้งหมด
- จำนวน ack ทั้งหมด
- จำนวน follower ที่เชื่อมอยู่
- follower ids ที่ active
- pending deliveries

### `GET /admin/events`

ใช้ดูรายการ event พร้อม ack ของแต่ละ follower

### `GET /admin/followers`

ใช้ดู follower ที่เชื่อม WebSocket อยู่ในตอนนั้น

## WebSocket

follower ใช้ WebSocket endpoint นี้:

```text
ws://host:5990/ws/{follower_id}?token=change-me
```

server จะส่ง message แบบนี้:

```json
{
  "type": "event",
  "event": {
    "event_id": "open-15072",
    "action": "open",
    "symbol": "BTCUSD",
    "side": "sell",
    "volume": 1,
    "magic": 0,
    "timestamp": 1774677300,
    "sl": 0.0,
    "tp": 0.0
  }
}
```

follower ส่ง ack กลับแบบนี้:

```json
{
  "type": "ack",
  "event_id": "open-15072",
  "status": "done",
  "detail": "opened"
}
```

## หมายเหตุสำคัญ

- `server.py` ตอนนี้เก็บ event และ ack ไว้ใน memory เท่านั้น ถ้า server restart ข้อมูลจะหาย
- การปิด order ฝั่ง follower ใช้ `symbol` และ `side` เพื่อหา position ล่าสุดที่ตรงกัน
- ถ้ามีหลาย position ซ้อนกันใน `symbol` และ `side` เดียวกัน อาจปิดไม่ตรงไม้ที่ต้องการ
- ถ้า follower หลายเครื่องมีชื่อเครื่องซ้ำกัน ควรตั้ง `FOLLOWER_ID` เอง
- ถ้า MT5 ตอบ `No money` หรือ `retcode=10019` follower จะ mark event เป็น `ignored` เพื่อไม่ให้ retry ซ้ำรัว ๆ

## ไฟล์ที่ควรรู้

- [server.py](/D:/Coding/MT5_copy_server/server.py)
- [follower.py](/D:/Coding/MT5_copy_server/follower.py)
- [requirements.txt](/D:/Coding/MT5_copy_server/requirements.txt)
- [.gitignore](/D:/Coding/MT5_copy_server/.gitignore)
