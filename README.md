# MT5 Copy Server

โปรเจกต์นี้เป็นระบบ copy trade แบบง่ายระหว่าง API server และ MT5 follower

- `server.py` รับ event `open` และ `close` ผ่าน HTTP API
- `follower.py` ดึง event จาก server แล้วส่งคำสั่งเข้า MetaTrader 5
- รองรับหลาย follower พร้อมกันด้วย `follower_id` แยกตามเครื่อง

## โครงสร้างไฟล์

- `server.py` FastAPI server
- `follower.py` MT5 follower client
- `follower_state.json` state ของ follower สำหรับกัน event ซ้ำ

## การทำงานโดยย่อ

1. ส่ง event ไปที่ `POST /events`
2. follower แต่ละเครื่องดึง event ผ่าน `GET /pull?follower_id=...`
3. follower เปิดหรือปิด order ใน MT5
4. follower ส่งผลกลับผ่าน `POST /ack`

## ข้อกำหนด

- Python 3.14 หรือใกล้เคียง
- MetaTrader 5 Terminal
- Python package:
  - `fastapi`
  - `uvicorn`
  - `requests`
  - `MetaTrader5`

ติดตั้ง package:

```powershell
python -m pip install fastapi uvicorn requests MetaTrader5
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
- ไม่เปิด `open` event ที่เก่ากว่าเวลาเริ่มรัน
- ดึง event แยกตาม `follower_id`

## API

ทุก request ต้องส่ง header:

```text
Authorization: Bearer change-me
```

### `POST /events`

ใช้ส่ง event เข้า queue

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

### `GET /pull`

follower ใช้ดึง event ที่ยังไม่ถูก ack โดย `follower_id` นั้น

ตัวอย่าง:

```text
GET /pull?follower_id=mt5-01
```

### `POST /ack`

follower ใช้ส่งผลลัพธ์กลับ เช่น `done`, `ignored`, `error`

## หมายเหตุสำคัญ

- `server.py` เก็บ event และ ack ไว้ใน memory เท่านั้น ถ้า server restart ข้อมูลจะหาย
- การปิด order ฝั่ง follower ตอนนี้ใช้ `symbol` และ `side` เพื่อหา position ล่าสุดที่ตรงกัน
- ถ้ามีหลาย position ซ้อนกันใน `symbol` และ `side` เดียวกัน อาจปิดไม่ตรงไม้ที่ต้องการ
- ถ้าเครื่อง follower หลายเครื่องมีชื่อเครื่องซ้ำกัน ควรตั้ง `FOLLOWER_ID` เอง

## ไฟล์ที่ควรรู้

- [server.py](/D:/Coding/MT5_copy_server/server.py)
- [follower.py](/D:/Coding/MT5_copy_server/follower.py)
- [.gitignore](/D:/Coding/MT5_copy_server/.gitignore)
