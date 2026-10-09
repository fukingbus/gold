# 金色寶箱圖鑑

[開啟圖鑑](https://fukingbus.github.io/gold/)

飄流幻境 Re 全地圖「金色寶箱」座標圖鑑，沿用天啟小幫手與聖殿秘笈的深色、金色介面。
可垂直瀏覽、搜尋地圖、選取寶箱、縮放拖曳、開啟放大地圖，以及複製遊戲座標。

## 收錄範圍

- 來源版本：`1.3.19_d6bc1e25_e7df651c`。
- 完整嚴格解析 1,256 個 Eve 場景，結合 1,336 張場景定義與 7,543 個 NPC 定義。
- 58 個位置，分布於 57 張邏輯地圖。
- 55 張地圖使用遊戲原始小地圖；席貝兒的家與海風之屋使用同版本的導航網格繪圖。
- 54 個共用 PNG 檔案，共 5,585,158 bytes。

遊戲 NPC 名稱為「黃金寶箱」（19117），對話中的「金色寶箱鑰匙」對應本圖鑑的俗稱。
不同 NPC「黃金大寶箱」（16011）未混入。
澳洲山頂通道 10161、10162 分別使用其他場景的模型，但仍是獨立邏輯地圖。
10162 的寶箱位置未配置開箱互動，已在介面註明。

位置是客戶端預設資料；實際顯示、可開啟狀態、重生時間以伺服器為準。
導航替代圖只表示靜態快取區域，不是物理障礙圖或即時導航服務。
場景分區保留原始 `mapGroup` 編號，未臆測地理區域名稱。

## 資料與驗證

`data/chests.json` 與 `data/maps.json` 為網站資料；`evidence/` 保留精確來源雜湊、
場景索引、58 組座標位元組、完整相關事件及地圖輸出證據。
Lua 載入以 `eventNumber` 作為執行時場景鍵，不能以可能重複的 `mapNumber` 合併地圖。

小地圖使用實際客戶端等比例投影，`scale = 1000 / max(mapWidth, mapHeight)`：

```text
imageX = gameX * scale + SmallMapOffsetX
imageY = 1024 - (mapHeight - gameY) * scale - SmallMapOffsetY
```

此公式由重新驗證的 Stage/UIMain Lua 確認；不可改為 X/Y 各自拉伸。
所有 58 個標記均落在圖片邊界內。
已安裝 Lua 與 1.3.19 封裝資源的版本差異在證據中保留，不宣稱遊戲即時狀態。

## 本機預覽

使用 Node.js 20 以上，無需安裝 npm 相依套件。

```sh
npm run check
npm run build
npm run preview
```

預覽網址為 `http://127.0.0.1:11329/gold/`，Ctrl+C 停止。
`check` 驗證完整清單、獨立座標位元組、倍率、投影、圖片尺寸與 SHA-256。
`build` 僅將網站所需檔案放入 `dist/`；原始 bundle、模型及分析資料不會放入 Pages 成品。
推送 `main` 後，GitHub Actions 自動檢查、建立並部署 GitHub Pages。

`scripts/extract_chests.py` 與 `scripts/export_maps.py` 可使用指定的本機 WLRE 資料管線重建資料。
它們需要 Python 3.12+、WLRE 套件相依套件與對應版本的封裝資源；詳見各腳本 `--help`。
原始遊戲封裝未隨 repository 發布。

## 著作權

美術素材、數據資料著作權所有 中華網龍股份有限公司。
Non-profit publication, provided for reference and educational purposes only.
This project is not affiliated with or endorsed by Chinesegamer. All game assets
and data are copyright © Chinesegamer International Corp.
