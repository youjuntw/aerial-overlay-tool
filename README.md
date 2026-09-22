# 空拍套疊工具 · Aerial Overlay Tool

把無人機空拍照拼成一張近正射底圖,再把 CAD/PDF 施工圖依圖層以**剛體(不變形)**方式套疊上去,用來做工程進度記錄與簡報。

> **English:** A small offline tool for construction progress documentation. It stitches DJI drone photos into a near-orthographic base image, then overlays selected layers of a construction drawing (PDF) using a **rigid (similarity) transform only** — the drawing is never skewed or distorted. Windows-first, Traditional-Chinese UI. Runs fully locally, no network, no accounts.

---

## 這工具解決什麼問題

- 每期空拍要跟施工圖對照,手動 PS 疊圖很慢又容易把圖拉歪。
- 這支工具:**拼接 → 拖 4 個基地角 → 自動輸出套疊圖**,施工圖全程保持原形狀(只做旋轉+等比縮放+平移)。

## 特色

- **施工圖絕不變形**:用剛體(similarity)轉換,不用會使圖走樣的 affine/homography/TPS。
- **自動篩照片**:排除傾斜照(雲台俯角未達 −80°);若照片都有 DJI 相對高度,自動保留張數最多的同高度航線,剔除混入的補拍/相鄰地塊。
- **智慧接縫**:SIFT + RANSAC 全仿射對位,加合理性/支撐雙重檢查與輕量全域平差;合成用 graph-cut 接縫 + 多頻段混合(退路為硬選),盡量無縫。
- **依圖層抽線**:只抽你要的圖層(施工圍籬、洗車台、PC 樁…),可自訂。
- **瀏覽器對位**:產生一個 `picker.html`,拖 4 個角、附放大鏡,按鈕下載 `points.txt`。
- **中文路徑安全**:輸出用 `imencode`+開檔,避開 OpenCV 在中文路徑靜默失敗的雷。

## 需求

- Windows(用到系統中文字型;其他 OS 可自行改字型路徑)
- Python 3.10+
- 套件:

```bash
pip install -r requirements.txt
```

> 拼接的接縫混合用到 `cv2.detail`(GraphCut / MultiBandBlender),標準 `opencv-python-headless` 已內含;若該環境沒有,程式會自動退回硬選合成。

## 快速開始

### 方式 A:雙擊(最簡單)
雙擊 `雙擊執行.bat` → 黑視窗出選單 → 選 **[1] 只合成** / **[2] 合成+對位工具** / **[3] 出套疊圖**。
會用你電腦 PATH 的 `py`/`python` 執行,並跳出正常的「選擇資料夾/檔案」視窗。

### 方式 B:命令列(三步)

```bash
# 1) 拼接空拍 → 輸出\<資料夾名>\mosaic.png
python 空拍套疊工具.py stitch  "你的照片資料夾"

# 2) 產對位工具 → picker.html + 圍籬編號對照圖.png
python 空拍套疊工具.py prep    "輸出\<資料夾名>\mosaic.png"  "施工圖.pdf"  --page 3
#    用瀏覽器開 picker.html,拖 4 個基地角,按【下載 points.txt】(存到 mosaic.png 同資料夾)

# 3) 套疊 → 套疊.png + 套疊_透明層.png(並印出殘差)
python 空拍套疊工具.py overlay "輸出\<資料夾名>\mosaic.png"  "施工圖.pdf"  "...\points.txt"  --page 3
```

## 自訂圖層

預設抽這些圖層:`施工圍籬,洗車台,鐵絲網,鐵欄,水泥牆,S-CONCP`。
你的圖層命名不同時,用 `--layers` 指定,並用 `--fence-layer` 指定用來抓 4 角的主圍籬層:

```bash
python 空拍套疊工具.py overlay mosaic.png plan.pdf points.txt \
  --page 3 --fence-layer 施工圍籬 --layers "施工圍籬,洗車台,S-CONCP"
```

`--page` 是施工圖頁碼(0 起算);圖層名稱需與 PDF 內的 OCG/圖層名一致。

## 限制(重要)

- 這是**視覺進度底圖**,不是量測級正射影像。無人機無 RTK 時定位約公尺級,套圖以對齊看得見的地物為準,**不可作量測或契約座標依據**。
- 2D 成對拼接在高低起伏大的工地會有視差,少數接縫可能有落差。要**量測級/完全無縫**,請改用 DJI Terra 或 WebODM 出正射影像後,再用本工具的 `prep`/`overlay` 套疊(見 `docs/`)。

## 自己打包成 .exe(給沒裝 Python 的同事)

```bash
pip install pyinstaller
pyinstaller --onefile --console --collect-all pymupdf --collect-all cv2 空拍套疊工具.py
```

> 打包出的 `.exe` 會內嵌你的建置路徑(含使用者名),且檔案很大,**不建議 commit 進版本庫**;請透過雲端或 GitHub Releases 發佈。
> 註:PyInstaller 打包後,`tkinter` 檔案對話框在部分環境不穩;`.exe` 版會改用「把資料夾/檔案拖進黑視窗」的方式輸入路徑。自己有裝 Python 的話,直接用 `雙擊執行.bat` 跑 `.py` 體驗最好。

## 空拍怎麼拍(DJI 教學)

`docs/` 內附三份繁中說明:
- `Mini5Pro_手動測繪說明書_RC2.html` — DJI Mini 5 Pro + RC 2(遙控器不能裝 App)的手動測繪流程。
- `Mini5Pro_空拍正射_SOP.html` — 用第三方 App 自動網格航線的正射 SOP。
- `DJI_Terra_出正射_操作步驟.txt` — 用 DJI Terra 出量測級正射底圖。

## 授權

[MIT](LICENSE)。歡迎自由使用、修改、散布。
