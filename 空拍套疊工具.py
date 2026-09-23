# -*- coding: utf-8 -*-
"""
空拍套疊工具 —— 把無人機空拍照拼成完整正射底圖,並將施工圖(依圖層)以「剛體(不變形)」
套疊上去。適合每期空拍重複使用。

流程(三步,依序執行):
  1) 拼接:   python 空拍套疊工具.py stitch  <照片資料夾>
             → 自動排除傾斜照與不同高度的離群航線,SIFT 拼成 mosaic.png
  2) 產工具: python 空拍套疊工具.py prep    <mosaic.png> <施工圖.pdf> [--page 3]
             → 自動抓圍籬 4 角,產生 picker.html + 圍籬編號對照圖.png
             → 用瀏覽器開 picker.html,把 4 點拖到底圖對應基地角,按「下載 points.txt」
  3) 套疊:   python 空拍套疊工具.py overlay <mosaic.png> <施工圖.pdf> <points.txt> [--page 3]
             → 剛體套疊 → 套疊.png(合成)+ 套疊_透明層.png + 殘差

圖層預設:施工圍籬 洗車台 鐵絲網(透空圍籬) 鐵欄 水泥牆 S-CONCP(=PC樁)
可用 --layers "施工圍籬,洗車台,S-CONCP" 自訂。
需求:pip install pymupdf opencv-python-headless pillow numpy
"""
import os, io, sys, re, glob, argparse, base64
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageFont, ImageFilter

DEFAULT_LAYERS = "施工圍籬,洗車台,鐵絲網,鐵欄,水泥牆,S-CONCP"

# ---------- 共用 ----------
def cjk_font(sz):
    for fp in ["C:/Windows/Fonts/msjh.ttc","C:/Windows/Fonts/mingliu.ttc","C:/Windows/Fonts/simsun.ttc","arial.ttf"]:
        try: return ImageFont.truetype(fp, sz)
        except Exception: pass
    return ImageFont.load_default()

def dji_xmp(path):
    """讀取 DJI XMP 中、可用於判斷同一航線的拍攝資訊。"""
    try:
        d=open(path,"rb").read(200000)
        s=d.find(b"<x:xmpmeta"); x=d[s:s+8000].decode("utf-8","ignore") if s>0 else ""
        def get(name):
            m=re.search(r'drone-dji:%s\s*=\s*"([-+\d.]+)"'%name,x)
            return float(m.group(1)) if m else None
        return {"pitch":get("GimbalPitchDegree"),"altitude":get("RelativeAltitude")}
    except Exception:
        return {"pitch":None,"altitude":None}

def _keep_main_altitude_group(items, gap_m=3.0):
    """保留張數最多且高度連續的一組 DJI 照片。

    同一資料夾常會混入補拍或相鄰地塊照片；這些照片的雲台同為正下方，
    但飛行高度不同，不能放進同一張正射式拼接圖。僅在每張都讀得到
    DJI 相對高度且至少有三張時啟用，避免誤傷非 DJI 或缺少 XMP 的照片。
    """
    if len(items)<3 or any(meta["altitude"] is None for _,meta in items):
        return items
    ordered=sorted(items,key=lambda x:x[1]["altitude"])
    groups=[[ordered[0]]]
    for item in ordered[1:]:
        if item[1]["altitude"]-groups[-1][-1][1]["altitude"]<=gap_m:
            groups[-1].append(item)
        else:
            groups.append([item])
    chosen=max(groups,key=len)
    if len(chosen)==len(items) or len(chosen)<3:
        return items
    selected={os.path.normcase(os.path.abspath(f)) for f,_ in chosen}
    low=min(meta["altitude"] for _,meta in chosen); high=max(meta["altitude"] for _,meta in chosen)
    print("  主航線相對高度 %.1f–%.1f m，共 %d 張"%(low,high,len(chosen)),flush=True)
    for f,meta in items:
        if os.path.normcase(os.path.abspath(f)) not in selected:
            print("  排除不同高度航線:",os.path.basename(f),"relative altitude",meta["altitude"],"m",flush=True)
    return [(f,meta) for f,meta in items if os.path.normcase(os.path.abspath(f)) in selected]

def tool_dir():
    if getattr(sys,"frozen",False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# ---------- 1) 拼接 ----------
def cmd_stitch(a):
    import cv2
    folder=a.input
    seen=set(); files=[]
    for f in glob.glob(os.path.join(folder,"*.JPG"))+glob.glob(os.path.join(folder,"*.jpg")):
        k=os.path.normcase(os.path.abspath(f))   # Windows 不分大小寫,去重
        if k not in seen: seen.add(k); files.append(f)
    files=sorted(files)
    candidates=[]
    for f in files:
        meta=dji_xmp(f); p=meta["pitch"]
        if p is None or p < -80:   # 近正射才收(俯角接近-90)
            candidates.append((f,meta))
        else:
            print("  排除傾斜照:", os.path.basename(f), "pitch", p)
    keep_meta=_keep_main_altitude_group(candidates)
    keep=[f for f,_ in keep_meta]
    if not files:
        print("⚠ 找不到 .JPG/.jpg 照片,請確認資料夾路徑。"); return
    if not keep:
        print("⚠ 這批照片全部是傾斜照(俯角未達 -80°),不是正射影像 ——")
        print("   拼出來不會是正射底圖,套施工圖會對不準。正射照的俯角應接近 -90°。")
        force=getattr(a,"all",False)
        if not force:
            try: force=input("   仍要用『全部照片』強制拼接嗎?(y/N):").strip().lower()=="y"
            except Exception: force=False
        if not force: print("已取消拼接。"); return
        keep=files
    print("拼接使用", len(keep), "張", flush=True)
    print("讀取照片並縮圖中…(整個拼接通常 10~60 秒)", flush=True)
    def load(f,long=3000):
        im=ImageOps.exif_transpose(Image.open(f)).convert("RGB")
        s=long/max(im.size); im=im.resize((int(im.width*s),int(im.height*s)),Image.LANCZOS)
        return cv2.cvtColor(np.array(im),cv2.COLOR_RGB2BGR)
    imgs=[]
    for i,f in enumerate(keep,1):
        print("  載入 %d/%d  %s"%(i,len(keep),os.path.basename(f)), flush=True)
        imgs.append(load(f))
    print("開始拼接運算(SIFT 特徵對位)…", flush=True)
    pano,placed,total=incremental_mosaic(imgs, cv2)
    print("  已拼接 %d/%d 張"%(placed,total), flush=True)
    if placed<total:
        print("  (%d 張重疊不足未拼上,通常不影響基地周界)"%(total-placed), flush=True)
    # 裁黑邊
    a2=pano; mask=a2.max(2)>12; ys,xs=np.nonzero(mask)
    pano=pano[ys.min()+6:ys.max()-6, xs.min()+6:xs.max()-6]
    name=os.path.basename(os.path.abspath(folder).rstrip("/\\")) or "空拍"
    outdir=os.path.join(tool_dir(),"輸出",name); os.makedirs(outdir,exist_ok=True)
    out=os.path.join(outdir,"mosaic.png")
    ok,buf=cv2.imencode(".png",pano)   # cv2.imwrite 不支援中文路徑,改用 imencode+開檔
    if not ok:
        print("⚠ 影像編碼失敗,未能輸出 mosaic.png。", flush=True); return None
    with open(out,"wb") as fp: fp.write(buf.tobytes())
    print("輸出:", out, pano.shape[1],"x",pano.shape[0], flush=True)
    print("(產出都放在工具旁的『輸出\\%s』,不會動到原始照片夾)"%name, flush=True)
    return out

def _affine_sanity(M, scale=(0.8,1.25), max_aniso=1.10, max_rot=15.0):
    """檢查 2x3 仿射的 2x2 線性部分是否像『近正射、同高度、同 yaw』該有的樣子。"""
    A=np.asarray(M,np.float64)[:2,:2]
    det=float(np.linalg.det(A))
    if not np.isfinite(det) or det<=0: return False,"鏡射/退化 det=%.3f"%det
    U,s,Vt=np.linalg.svd(A)
    if not (scale[0]<=s[1] and s[0]<=scale[1]): return False,"縮放 %.3f~%.3f 超界"%(s[1],s[0])
    if s[0]/s[1]>max_aniso: return False,"各向異性 %.3f 超界(剪切/非等比縮放)"%(s[0]/s[1])
    R=U@Vt; ang=float(np.degrees(np.arctan2(R[1,0],R[0,0])))
    if abs(ang)>max_rot: return False,"旋轉 %.1f° 超界"%ang
    return True,"s=%.3f/%.3f rot=%+.1f°"%(s[0],s[1],ang)

def _support_ok(src_in, dst_in, h, w, min_std_frac=0.03, max_split_px=25.0, rng=None):
    """內點『支撐』是否足以決定整張影像的仿射(整塊錯位的真正防線)。"""
    rng=rng if rng is not None else np.random.default_rng(0)
    p=src_in-src_in.mean(0); ev=np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov(p.T)),0))
    if ev[0]<min_std_frac*min(h,w): return False,"內點分布過窄 std=%.0f/%.0f px"%(ev[0],ev[1])
    n=len(src_in); idx=rng.permutation(n); C=np.float64([[0,0,1],[w,0,1],[w,h,1],[0,h,1]])
    def fit(ix):
        X=np.linalg.lstsq(np.c_[src_in[ix],np.ones(len(ix))],dst_in[ix],rcond=None)[0]; return C@X
    d=float(np.linalg.norm(fit(idx[:n//2])-fit(idx[n//2:]),axis=1).max())
    if d>max_split_px: return False,"對半擬合四角差 %.0f px(支撐不穩)"%d
    return True,"std=%.0f/%.0f 對半差 %.1fpx"%(ev[0],ev[1],d)

def _ratio_matches(bf, qdes, tdes, tpts, ratio=0.7, k=5, dup_px=30.0):
    """比值檢定,但忽略『假競爭者』(同一地物在多張已拼影像各有副本)。"""
    ms=bf.knnMatch(qdes,tdes,k=k); good=[]
    if not ms: return good
    kk=min(len(m) for m in ms)
    if kk<2: return [m[0] for m in ms if m]
    ti=np.array([[m.trainIdx for m in row[:kk]] for row in ms],np.int64)
    dd=np.array([[m.distance for m in row[:kk]] for row in ms],np.float32)
    far=np.abs(tpts[ti]-tpts[ti[:,:1]]).max(2)>dup_px
    far[:,0]=False; has=far.any(1); j2=np.where(has,far.argmax(1),0)
    d2=dd[np.arange(len(ms)),j2]
    keep=(~has)|(dd[:,0]<ratio*d2)
    return [ms[q][0] for q in np.flatnonzero(keep)]

def _global_adjust(H, links, ref, hw, iters=3, huber=4.0, prior=3000.0, max_per_pair=300, rng=None, verbose=True):
    """輕量全域平差:固定 ref,用全部成對內點對所有仿射參數做 Huber 加權最小平方聯合微調。"""
    rng=rng if rng is not None else np.random.default_rng(0)
    ids=[i for i in H if i!=ref]
    if not ids or not links: return H
    col={i:3*k for k,i in enumerate(ids)}; N=3*len(ids); rows=[]; rhs=[]
    for i,P,J,Q in links:
        for j in np.unique(J):
            m=np.flatnonzero(J==j)
            if len(m)>max_per_pair: m=rng.choice(m,max_per_pair,replace=False)
            A=np.zeros((len(m),N)); Pm=P[m].astype(np.float64); Qm=Q[m].astype(np.float64)
            A[:,col[i]:col[i]+3]=np.c_[Pm,np.ones(len(m))]
            if j==ref: rhs.append(Qm)
            else: A[:,col[j]:col[j]+3]=-np.c_[Qm,np.ones(len(m))]; rhs.append(np.zeros((len(m),2)))
            rows.append(A)
    A=np.vstack(rows); R=np.vstack(rhs); nd=len(A)
    Ap=np.zeros((2*len(ids),N)); Rp=np.zeros((2*len(ids),2)); X0=np.zeros((N,2))
    for k,i in enumerate(ids):
        c=col[i]; X0[c]=(H[i][0,0],H[i][1,0]); X0[c+1]=(H[i][0,1],H[i][1,1]); X0[c+2]=(H[i][0,2],H[i][1,2])
        Ap[2*k,c]=prior; Ap[2*k+1,c+1]=prior; Rp[2*k]=prior*X0[c]; Rp[2*k+1]=prior*X0[c+1]
    A=np.vstack([A,Ap]); R=np.vstack([R,Rp]); w=np.ones(len(A)); X=X0
    res0=np.linalg.norm(A[:nd]@X0-R[:nd],axis=1)
    for _ in range(iters):
        res=np.linalg.norm(A[:nd]@X-R[:nd],axis=1)
        w[:nd]=np.minimum(1.0,huber/np.maximum(res,1e-9))**0.5
        X=np.linalg.lstsq(A*w[:,None],R*w[:,None],rcond=None)[0]
    res1=np.linalg.norm(A[:nd]@X-R[:nd],axis=1)
    newH={ref:H[ref]}; shift=0.0
    for i in ids:
        c=col[i]; Hn=np.array([[X[c,0],X[c+1,0],X[c+2,0]],[X[c,1],X[c+1,1],X[c+2,1]],[0,0,1]],np.float64)
        ok,why=_affine_sanity(Hn)
        if not ok:
            if verbose: print("  平差後 #%d 不合理(%s),放棄本次平差"%(i+1,why),flush=True)
            return H
        h,w=hw[i]; shift=max(shift,float(np.abs((Hn-H[i])[:2]@np.array([[0,0,1],[w,0,1],[w,h,1],[0,h,1]],np.float64).T).max()))
        newH[i]=Hn
    if verbose: print("  全域平差:內點殘差 中位數 %.2f→%.2f px,最大 %.1f→%.1f px,影像最大移動 %.1f px"%(
        np.median(res0),np.median(res1),res0.max(),res1.max(),shift),flush=True)
    return newH

# ---------- 拼接:合成 ----------
def _canvas_frame(imgs, H, cv2):
    """所有已放影像外接框 → 平移矩陣 T 與畫布大小 (W,Hc)。"""
    allc=[]
    for i in H:
        h,w=imgs[i].shape[:2]; c=np.float32([[0,0],[w,0],[w,h],[0,h]]).reshape(-1,1,2)
        allc.append(cv2.perspectiveTransform(c,H[i]))
    allc=np.concatenate(allc); xmin,ymin=allc.min(0).ravel(); xmax,ymax=allc.max(0).ravel()
    T=np.array([[1,0,-xmin],[0,1,-ymin],[0,0,1]],np.float64)
    return T,int(np.ceil(xmax-xmin)),int(np.ceil(ymax-ymin))

def _compose_hard(imgs, H, cv2):
    """硬選:每個像素取「距自身邊界最遠(最靠中心)」的那張,不平均(舊做法,現在是退路)。"""
    T,W,Hc=_canvas_frame(imgs,H,cv2)
    canvas=np.zeros((Hc,W,3),np.uint8); best=np.full((Hc,W),-1.0,np.float32)
    for i in H:
        h,w=imgs[i].shape[:2]
        wt=np.ones((h,w),np.uint8); wt[0,:]=0; wt[-1,:]=0; wt[:,0]=0; wt[:,-1]=0
        wt=cv2.distanceTransform(wt,cv2.DIST_L2,3).astype(np.float32)
        M=(T@H[i])[:2]
        wimg=cv2.warpAffine(imgs[i],M,(W,Hc))
        wwt=cv2.warpAffine(wt,M,(W,Hc)); wwt[wimg.sum(2)==0]=-1
        take=wwt>best; canvas[take]=wimg[take]; best[take]=wwt[take]
    return canvas

def _compose_seams(imgs, H, cv2, verbose=True, seam_mp=0.25, blend_strength=5.0, exposure=True):
    """智慧接縫 + 多頻段混合(cv2.detail):
    1) 每張只 warp 自己的外接框 ROI(記 top-left),遮罩去掉插值造成的暗邊;
    2) 縮到接縫尺度(每張約 seam_mp 百萬像素)估區塊增益曝光補償、跑 GraphCut 接縫(失敗退 DP);
    3) 接縫遮罩放大回全解析度,套曝光增益,餵 MultiBandBlender:高頻只在接縫 1~2 px 切換(保持銳利),
       低頻(亮度/色調)在數十到上百 px 內漸變 → 階差消失但不整片糊。
    任何步驟失敗直接丟例外,由呼叫端退回 _compose_hard。"""
    det=cv2.detail
    ids=sorted(H); T,W,Hc=_canvas_frame(imgs,H,cv2)
    warps=[]; masks=[]; tls=[]
    for i in ids:
        h,w=imgs[i].shape[:2]
        c=(T@H[i])@np.array([[0,w,w,0],[0,0,h,h],[1,1,1,1]],np.float64)
        x0,y0=max(int(np.floor(c[0].min())),0),max(int(np.floor(c[1].min())),0)
        x1,y1=min(int(np.ceil(c[0].max())),W),min(int(np.ceil(c[1].max())),Hc)
        M=(np.array([[1,0,-x0],[0,1,-y0],[0,0,1]],np.float64)@T@H[i])[:2]
        warps.append(cv2.warpAffine(imgs[i],M,(x1-x0,y1-y0),flags=cv2.INTER_LINEAR))
        m=cv2.warpAffine(np.full((h,w),255,np.uint8),M,(x1-x0,y1-y0),flags=cv2.INTER_LINEAR)
        masks.append(cv2.erode(((m>=250)*255).astype(np.uint8),np.ones((3,3),np.uint8)))
        tls.append((x0,y0))
    # 接縫尺度
    s=min(1.0,float(np.sqrt(seam_mp*1e6/max(im.shape[0]*im.shape[1] for im in imgs))))
    sm_imgs=[]; sm_masks=[]; sm_tls=[]
    for img,m,(x0,y0) in zip(warps,masks,tls):
        sw,sh=max(2,int(round(img.shape[1]*s))),max(2,int(round(img.shape[0]*s)))
        sm_imgs.append(cv2.resize(img,(sw,sh),interpolation=cv2.INTER_AREA))
        sm_masks.append(cv2.resize(m,(sw,sh),interpolation=cv2.INTER_NEAREST))
        sm_tls.append((int(round(x0*s)),int(round(y0*s))))
    # 曝光補償(區塊增益):在接縫尺度估,套用到全解析度
    comp=None
    if exposure:
        try:
            comp=det.ExposureCompensator_createDefault(det.ExposureCompensator_GAIN_BLOCKS)
            comp.feed(sm_tls,sm_imgs,sm_masks)
            sm_imgs=[comp.apply(k,sm_tls[k],sm_imgs[k],sm_masks[k]) for k in range(len(ids))]
        except Exception as e:
            comp=None
            if verbose: print("  曝光補償略過(%s)"%e,flush=True)
    # 接縫:GraphCut(失敗退 DP)
    fimgs=[im.astype(np.float32) for im in sm_imgs]
    try:
        seam=det.GraphCutSeamFinder("COST_COLOR").find(fimgs,sm_tls,[m.copy() for m in sm_masks]); kind="GraphCut"
    except Exception as e:
        if verbose: print("  GraphCut 接縫失敗(%s),改用 DP"%e,flush=True)
        seam=det.DpSeamFinder("COLOR_GRAD").find(fimgs,sm_tls,[m.copy() for m in sm_masks]); kind="DP"
    seam=[np.asarray(x.get() if hasattr(x,"get") else x) for x in seam]
    # 多頻段混合
    nb=int(np.clip(np.ceil(np.log2(max(np.sqrt(W*Hc)*blend_strength/100.0,2))),4,7))
    # GAIN:cv2 的 MultiBandBlender 用 int16 存拉普拉斯金字塔,最後正規化時每個係數截斷掉 1 單位(每層都截),
    # 細節對比會微幅變鈍;先乘 16 再餵(255*16 仍在 int16 內),輸出除回來,截斷誤差變成 1/16 單位。
    GAIN=16
    blender=det.MultiBandBlender(0,nb,cv2.CV_32F); blender.prepare((0,0,W,Hc))
    for k in range(len(ids)):
        img=warps[k]
        if comp is not None: img=comp.apply(k,tls[k],img,masks[k])
        sk=cv2.resize(cv2.dilate(seam[k],np.ones((3,3),np.uint8)),(img.shape[1],img.shape[0]),interpolation=cv2.INTER_LINEAR)
        blender.feed(img.astype(np.int16)*GAIN,cv2.bitwise_and(sk,masks[k]),tls[k])
    res,_=blender.blend(None,None)
    canvas=np.clip((np.asarray(res).astype(np.float32)+GAIN/2)/GAIN,0,255).astype(np.uint8)
    if verbose: print("  合成:%s 接縫(尺度 %.2f)+ 多頻段混合 %d 段%s,畫布 %dx%d"%(kind,s,nb," + 區塊增益曝光補償" if comp is not None else "",W,Hc),flush=True)
    return canvas

def incremental_mosaic(imgs, cv2, verbose=True):
    """增量式拼接:SIFT + 全仿射(RANSAC),過『SVD 合理性 + 內點支撐』雙重檢查才併入;最後輕量全域平差。
    合成用 graph-cut 智慧接縫 + 多頻段混合(沒有 cv2.detail 或失敗時退回硬選)。
    回傳 (mosaic, 已拼張數, 總張數)。"""
    rng=np.random.default_rng(0)
    sift=cv2.SIFT_create(3000); kp=[]; des=[]
    for im in imgs:
        k,d=sift.detectAndCompute(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),None); kp.append(k); des.append(d)
    n=len(imgs); ref=n//2; H={ref:np.eye(3)}
    gpts=[]; gdes=[]; gsrc=[]; gimg=[]; links=[]
    def add_placed(i):
        pts=np.array([p.pt for p in kp[i]],np.float32); ph=np.c_[pts,np.ones(len(pts))]@H[i].T
        gpts.append(ph[:,:2].astype(np.float32)); gdes.append(des[i]); gsrc.append(pts); gimg.append(np.full(len(pts),i,np.int32))
    add_placed(ref); bf=cv2.BFMatcher(cv2.NORM_L2); remaining=set(range(n))-{ref}; passes=0
    while remaining and passes<n:
        passes+=1; allpts=np.vstack(gpts); alldes=np.vstack(gdes); allsrc=np.vstack(gsrc); allimg=np.concatenate(gimg); progressed=False
        for i in sorted(remaining):
            if des[i] is None or len(des[i])<30: remaining.discard(i); continue
            h,w=imgs[i].shape[:2]
            good=_ratio_matches(bf,des[i],alldes,allpts)
            if len(good)<30: continue
            src=np.float32([kp[i][m.queryIdx].pt for m in good]); ti=np.int32([m.trainIdx for m in good]); dst=allpts[ti]
            M,inl=cv2.estimateAffine2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=5.0,maxIters=5000,confidence=0.995,refineIters=20)
            if M is None or inl is None: continue
            inl=inl.ravel().astype(bool); ni=int(inl.sum())
            if ni<30 or ni<0.15*len(good):
                if verbose: print("    #%d 內點不足 %d/%d,暫不放"%(i+1,ni,len(good)),flush=True)
                continue
            ok,why=_affine_sanity(M)
            if not ok:
                if verbose: print("    #%d 拒絕:%s"%(i+1,why),flush=True)
                continue
            ok,why2=_support_ok(src[inl],dst[inl],h,w,rng=rng)
            if not ok:
                if verbose: print("    #%d 拒絕:%s"%(i+1,why2),flush=True)
                continue
            H[i]=np.vstack([M,[0,0,1]]); add_placed(i); remaining.discard(i); progressed=True
            links.append((i,src[inl],allimg[ti[inl]],allsrc[ti[inl]]))
            if verbose: print("    拼上 #%d  內點 %d/%d  %s  %s"%(i+1,ni,len(good),why,why2),flush=True)
        if not progressed: break
    if len(H)>1: H=_global_adjust(H,links,ref,{i:imgs[i].shape[:2] for i in H},rng=rng,verbose=verbose)
    # 合成:graph-cut 智慧接縫 + 多頻段混合(cv2.detail);這台 build 沒有 detail、記憶體不足或任何一步出錯 → 退回硬選
    try:
        canvas=_compose_seams(imgs,H,cv2,verbose=verbose)
    except Exception as e:
        if verbose: print("  智慧接縫/混合失敗(%s: %s),退回硬選合成"%(type(e).__name__,e),flush=True)
        canvas=_compose_hard(imgs,H,cv2)
    return canvas, len(H), n

# ---------- PDF 圖層 → 紅線 ----------
def render_layers(pdf, page, layers, scale=3.0):
    import fitz
    doc=fitz.open(pdf); p=doc[page]; dr=p.get_drawings(); MB=p.mediabox
    sub=[d for d in dr if d.get("layer") in layers]
    nd=fitz.open(); pg=nd.new_page(width=MB.width,height=MB.height); sh=pg.new_shape()
    for d in sub:
        for it in d["items"]:
            op=it[0]
            if op=="l": sh.draw_line(it[1],it[2])
            elif op=="c": sh.draw_bezier(it[1],it[2],it[3],it[4])
            elif op=="re": sh.draw_rect(it[1])
            elif op=="qu": sh.draw_quad(it[1])
        col=(0,0,0) if d.get("color") is not None else None
        fil=(0,0,0) if d.get("fill") is not None else None
        sh.finish(color=col,fill=fil,width=max(d.get("width") or .5,.4),
                  closePath=d.get("closePath",False),even_odd=d.get("even_odd",True))
    sh.commit(); pg.set_rotation(p.rotation)
    pix=pg.get_pixmap(matrix=fitz.Matrix(scale,scale))
    return Image.frombytes("RGB",[pix.width,pix.height],pix.samples)

def _components(mask):
    h,w=mask.shape; vis=np.zeros_like(mask,bool); comps=[]
    for sy,sx in np.argwhere(mask):
        if vis[sy,sx]: continue
        st=[(sy,sx)]; vis[sy,sx]=True; c=[]
        while st:
            y,x=st.pop(); c.append((y,x))
            for ny in range(max(0,y-1),min(h,y+2)):
                for nx in range(max(0,x-1),min(w,x+2)):
                    if mask[ny,nx] and not vis[ny,nx]: vis[ny,nx]=True; st.append((ny,nx))
        comps.append(c)
    comps.sort(key=len,reverse=True); return comps

def auto_corners(fence_img):
    """自動抓圍籬 4 角(對角極值,取最大連通結構避開零星雜點)。回傳比例座標。"""
    f=fence_img.width/1191
    ds=fence_img.convert("L").resize((1191,int(fence_img.height/f)),Image.BILINEAR).filter(ImageFilter.MinFilter(5))
    mask=np.asarray(ds)<200; comps=_components(mask); thr=len(comps[0])*0.15
    pts=np.array([q for c in comps if len(c)>=thr for q in c])
    ys=pts[:,0].astype(float); xs=pts[:,1].astype(float); H,W=mask.shape
    s=xs+ys; d=xs-ys; frac=lambda i:(xs[i]/W, ys[i]/H)
    # 1左上 2右上 3右下(斜切) 4左下
    return [frac(np.argmin(s)), frac(np.argmax(d)), frac(np.argmax(s)), frac(np.argmin(d))]

def red_layer(img, whiteout=True):
    lum=np.asarray(img.convert("L"),np.float32); a=np.clip((200.0-lum)/200.0*255.0*1.7,0,255).astype(np.uint8)
    h,w=a.shape
    if whiteout:
        a[:,int(0.90*w):]=0; a[int(0.78*h):,int(0.55*w):int(0.90*w)]=0  # 去右側孤立虛線+右下圖例
    rgba=np.zeros((h,w,4),np.uint8); rgba[...,0]=220; rgba[...,3]=a
    out=Image.fromarray(rgba,"RGBA"); out.putalpha(out.getchannel("A").filter(ImageFilter.MaxFilter(3))); return out

# ---------- 2) 產工具 ----------
LABELS=["1 左上角","2 右上角","3 右下角(斜切)","4 左下角"]
def cmd_prep(a):
    base=Image.open(a.mosaic).convert("RGB"); bw,bh=base.size
    fence=render_layers(a.pdf,a.page,{a.fence_layer},scale=3.0)
    corners=auto_corners(fence)
    # 對照圖
    ref=fence.copy(); d=ImageDraw.Draw(ref); font=cjk_font(70)
    for (x,y),lab in zip(corners,LABELS):
        px,py=x*ref.width,y*ref.height
        d.ellipse((px-34,py-34,px+34,py+34),outline=(230,0,180),width=11)
        d.line((px-48,py,px+48,py),fill=(230,0,180),width=4); d.line((px,py-48,px,py+48),fill=(230,0,180),width=4)
        bb=d.textbbox((px+42,py-46),lab,font=font); d.rectangle((bb[0]-6,bb[1]-6,bb[2]+6,bb[3]+6),fill=(255,255,255))
        d.text((px+42,py-46),lab,fill=(210,0,160),font=font)
    fdir=os.path.dirname(os.path.abspath(a.mosaic))
    f=ref.width/2200; ref=ref.resize((2200,int(ref.height/f)),Image.LANCZOS); ref.save(os.path.join(fdir,"圍籬編號對照圖.png"))
    # 縮圖底圖 + 對照圖 -> base64
    f=base.width/1800; disp=base.resize((1800,int(base.height/f)),Image.LANCZOS)
    b1=io.BytesIO(); disp.save(b1,"PNG"); photo64=base64.b64encode(b1.getvalue()).decode()
    r2=ref.copy(); r2.thumbnail((1000,1000)); b2=io.BytesIO(); r2.save(b2,"PNG"); ref64=base64.b64encode(b2.getvalue()).decode()
    guess=[list(c) for c in corners]
    html=PICKER_HTML.replace("__PHOTO__",photo64).replace("__FENCE__",ref64)\
        .replace("__LABELS__",str(LABELS).replace("'",'"')).replace("__GUESS__",str(guess))\
        .replace("__BW__",str(disp.width)).replace("__BH__",str(disp.height))
    hp=os.path.join(fdir,"picker.html"); open(hp,"w",encoding="utf-8").write(html)
    print("產出:", hp, "與 圍籬編號對照圖.png")
    print("→ 瀏覽器開 picker.html,拖 4 點對齊後按「下載 points.txt」")
    print("下一步: python 空拍套疊工具.py overlay \"%s\" \"%s\" \"%s/points.txt\""%(a.mosaic,a.pdf,fdir))

# ---------- 3) 套疊 ----------
def sim_inv(fp,cp):
    z=fp[:,0]+1j*fp[:,1]; w=cp[:,0]+1j*cp[:,1]; zm,wm=z.mean(),w.mean(); dz,dw=z-zm,w-wm
    a=np.sum(np.conj(dz)*dw)/np.sum(np.abs(dz)**2); b=wm-a*zm
    res=np.abs(a*z+b-w); ia=1/a; ib=-b/a; p,q=ia.real,ia.imag
    return (p,-q,ib.real,q,p,ib.imag),abs(a),np.degrees(np.angle(a)),res

def parse_points(txt):
    return [(float(x),float(y)) for x,y in re.findall(r"\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)", txt)]

def cmd_overlay(a):
    layers=set(s.strip() for s in a.layers.split(","))
    base=Image.open(a.mosaic).convert("RGB"); cw,ch=base.size
    fence_ref=render_layers(a.pdf,a.page,{a.fence_layer},scale=3.0)
    fcorners=auto_corners(fence_ref)
    pcorners=parse_points(open(a.points,encoding="utf-8").read())
    if len(pcorners)!=len(fcorners):
        print("警告:points 點數(%d)與自動角點(%d)不符,取前 %d 個"%(len(pcorners),len(fcorners),min(len(pcorners),len(fcorners))))
    n=min(len(pcorners),len(fcorners))
    full=render_layers(a.pdf,a.page,layers,scale=3.0); fw,fh=full.size
    fp=np.array([(x*fw,y*fh) for x,y in fcorners[:n]]); cp=np.array([(x*cw,y*ch) for x,y in pcorners[:n]])
    coeffs,scale,ang,res=sim_inv(fp,cp); diag=np.hypot(*(cp.max(0)-cp.min(0)))
    overlay=red_layer(full, whiteout=not a.no_whiteout)
    warped=overlay.transform((cw,ch),Image.AFFINE,coeffs,resample=Image.BILINEAR)
    w80=warped.copy(); w80.putalpha(warped.getchannel("A").point(lambda v:round(v*0.8)))
    comp=base.convert("RGBA"); comp.alpha_composite(w80)
    fdir=os.path.dirname(os.path.abspath(a.mosaic))
    comp.convert("RGB").save(os.path.join(fdir,"套疊.png")); warped.save(os.path.join(fdir,"套疊_透明層.png"))
    print("輸出 套疊.png / 套疊_透明層.png")
    print("剛體殘差 mean=%.1fpx (%.2f%%) max=%.1f  scale=%.3f rot=%.2f (無變形)"%(res.mean(),res.mean()/diag*100,res.max(),scale,ang))

# ---------- HTML 點位工具模板 ----------
PICKER_HTML=r"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>對位控制點</title>
<style>body{font-family:"Microsoft JhengHei",sans-serif;margin:0;background:#1a1a1a;color:#eee}header{padding:10px 16px;background:#222}h1{font-size:16px;margin:0 0 4px}p.hint{margin:0;font-size:13px;color:#bbb}.wrap{display:flex;gap:12px;padding:12px;align-items:flex-start;flex-wrap:wrap}.panel{background:#222;border:1px solid #333;border-radius:8px;padding:10px}canvas{display:block;background:#000;cursor:crosshair;max-width:100%}.ref img{width:330px;max-width:100%}.side{width:300px}.plist{list-style:none;margin:0 0 10px;padding:0}.plist li{padding:6px 8px;border-radius:5px;cursor:pointer;font-size:14px;display:flex;justify-content:space-between}.plist li.active{background:#c0392b}textarea{width:100%;height:120px;background:#111;color:#8f8;border:1px solid #333;font-family:monospace;font-size:12px}button{background:#c0392b;color:#fff;border:0;padding:8px 12px;border-radius:5px;cursor:pointer;margin-top:6px}#loupe{position:fixed;width:190px;height:190px;border:2px solid #c0392b;border-radius:50%;pointer-events:none;display:none;z-index:9}</style></head><body>
<header><h1>對位控制點</h1><p class="hint">對照右邊「圍籬編號對照圖」,把 4 個編號點拖到底圖對應基地角。標記附近可拖;點空白處移動選取點;滑鼠移動有放大鏡。完成按「下載 points.txt」。</p></header>
<div class="wrap"><div class="panel"><canvas id="c"></canvas></div><div class="panel side"><ul class="plist" id="plist"></ul><textarea id="out" readonly></textarea><br><button id="copy">複製座標</button> <button id="dl">下載 points.txt</button></div><div class="panel ref"><div style="font-size:13px;color:#bbb;margin-bottom:6px">圍籬編號對照圖</div><img src="data:image/png;base64,__FENCE__"></div></div><canvas id="loupe"></canvas>
<script>const IW=__BW__,IH=__BH__,LABELS=__LABELS__;let pts=__GUESS__.map(p=>({x:p[0],y:p[1]}));const img=new Image();img.src="data:image/png;base64,__PHOTO__";const c=document.getElementById('c'),ctx=c.getContext('2d'),loupe=document.getElementById('loupe'),lctx=loupe.getContext('2d');loupe.width=190;loupe.height=190;let active=0,drag=false;
function layout(){const mw=Math.min(1400,window.innerWidth-700),s=Math.min(1,mw/IW);c.width=IW*s;c.height=IH*s;draw();}img.onload=layout;window.addEventListener('resize',layout);
function draw(){ctx.clearRect(0,0,c.width,c.height);ctx.drawImage(img,0,0,c.width,c.height);for(let i=0;i<pts.length;i++){const px=pts[i].x*c.width,py=pts[i].y*c.height;ctx.beginPath();ctx.arc(px,py,i==active?11:8,0,7);ctx.lineWidth=i==active?4:2.5;ctx.strokeStyle=i==active?'#ffec3d':'#ff2fd0';ctx.stroke();ctx.fillStyle=i==active?'#ffec3d':'#ff2fd0';ctx.font='bold 16px sans-serif';ctx.fillText(i+1,px+12,py-8);ctx.beginPath();ctx.moveTo(px-14,py);ctx.lineTo(px+14,py);ctx.moveTo(px,py-14);ctx.lineTo(px,py+14);ctx.lineWidth=1;ctx.stroke();}L();O();}
function L(){const u=document.getElementById('plist');u.innerHTML='';for(let i=0;i<pts.length;i++){const li=document.createElement('li');if(i==active)li.className='active';li.innerHTML='<span>'+LABELS[i]+'</span><span>'+pts[i].x.toFixed(3)+', '+pts[i].y.toFixed(3)+'</span>';li.onclick=()=>{active=i;draw();};u.appendChild(li);}}
function O(){let s='PHOTO_PTS_F = [\n';for(let i=0;i<pts.length;i++)s+='    ('+pts[i].x.toFixed(4)+', '+pts[i].y.toFixed(4)+'),  # '+LABELS[i]+'\n';s+=']';document.getElementById('out').value=s;}
function P(e){const r=c.getBoundingClientRect();return{x:(e.clientX-r.left)/c.width,y:(e.clientY-r.top)/c.height};}
c.addEventListener('mousedown',e=>{const p=P(e);let b=-1,bd=1e9;for(let i=0;i<pts.length;i++){const d=Math.hypot(pts[i].x-p.x,pts[i].y-p.y);if(d<bd){bd=d;b=i;}}if(bd*c.width<18)active=b;else pts[active]={x:p.x,y:p.y};drag=true;draw();lp(e);});
window.addEventListener('mousemove',e=>{if(drag){const p=P(e);pts[active]={x:Math.max(0,Math.min(1,p.x)),y:Math.max(0,Math.min(1,p.y))};draw();}if(e.target===c||drag)lp(e);else loupe.style.display='none';});
window.addEventListener('mouseup',()=>drag=false);
function lp(e){const r=c.getBoundingClientRect(),ins=e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom;if(!ins){loupe.style.display='none';return;}const fx=(e.clientX-r.left)/c.width,fy=(e.clientY-r.top)/c.height,sw=38;loupe.style.display='block';loupe.style.left=(e.clientX+20)+'px';loupe.style.top=(e.clientY+20)+'px';lctx.clearRect(0,0,190,190);lctx.drawImage(img,fx*IW-sw/2,fy*IH-sw/2,sw,sw,0,0,190,190);lctx.strokeStyle='#ffec3d';lctx.beginPath();lctx.moveTo(95,0);lctx.lineTo(95,190);lctx.moveTo(0,95);lctx.lineTo(190,95);lctx.stroke();}
document.getElementById('copy').onclick=()=>{const t=document.getElementById('out');t.select();document.execCommand('copy');};
document.getElementById('dl').onclick=()=>{const b=new Blob([document.getElementById('out').value],{type:'text/plain'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='points.txt';a.click();};
</script></body></html>"""

def ask_pdf_page():
    try:
        v=input("施工圖頁碼(0起算,直接 Enter = 3):").strip()
        return int(v) if v else 3
    except Exception: return 3

def _ask_path(prompt, kind):
    """最後備援:主控台輸入路徑(可把資料夾/檔案從檔案總管拖進視窗貼上)。"""
    print(prompt)
    s=input("  → ").strip().strip('"').strip("'").strip()
    if not s: return None
    if kind=="dir" and not os.path.isdir(s): print("  ⚠ 找不到資料夾:",s); return None
    if kind=="file" and not os.path.isfile(s): print("  ⚠ 找不到檔案:",s); return None
    return s

_PS_PICKER=r'''param([string]$Title="",[string]$Out="",[string]$Mode="dir",[string]$Filter="All files|*.*")
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$owner=New-Object System.Windows.Forms.Form; $owner.TopMost=$true; $owner.ShowInTaskbar=$false
function Save($p){ if($p){ [IO.File]::WriteAllText($Out,$p,[Text.Encoding]::UTF8) } }
if($Mode -eq "file"){
  $d=New-Object System.Windows.Forms.OpenFileDialog
  if($Title){ $d.Title=$Title }
  $d.Filter=$Filter; $d.CheckFileExists=$true
  if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){ Save $d.FileName }
  exit 0
}
$code=@'
using System;
using System.Runtime.InteropServices;
namespace NFP {
  public static class Dlg {
    public static string Show(string title){
      IntPtr hwnd=GetActiveWindow();
      IFileOpenDialog dlg=(IFileOpenDialog)new FileOpenDialogRCW();
      uint o; dlg.GetOptions(out o);
      dlg.SetOptions(o | 0x20 | 0x40);
      if(!string.IsNullOrEmpty(title)) dlg.SetTitle(title);
      int hr=dlg.Show(hwnd);
      if(hr!=0) return null;
      IShellItem item; dlg.GetResult(out item);
      string path; item.GetDisplayName(0x80058000, out path);
      return path;
    }
    [DllImport("user32.dll")] static extern IntPtr GetActiveWindow();
    [ComImport, ClassInterface(ClassInterfaceType.None), Guid("DC1C5A9C-E88A-4ADE-A5A1-60F82A20AEF7")] class FileOpenDialogRCW {}
    [ComImport, Guid("d57c7288-d4ad-4768-be02-9d969532d960"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IFileOpenDialog {
      [PreserveSig] int Show(IntPtr parent);
      void SetFileTypes(); void SetFileTypeIndex(); void GetFileTypeIndex();
      void Advise(); void Unadvise();
      void SetOptions(uint fos); void GetOptions(out uint pfos);
      void SetDefaultFolder(); void SetFolder(); void GetFolder(); void GetCurrentSelection();
      void SetFileName(); void GetFileName();
      void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string t);
      void SetOkButtonLabel(); void SetFileNameLabel();
      void GetResult(out IShellItem ppsi);
    }
    [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItem {
      void BindToHandler(); void GetParent();
      void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
    }
  }
}
'@
try{
  Add-Type -TypeDefinition $code -Language CSharp | Out-Null
  Save ([NFP.Dlg]::Show($Title))
}catch{
  $b=New-Object System.Windows.Forms.FolderBrowserDialog
  if($Title){ $b.Description=$Title }
  $b.ShowNewFolderButton=$false
  if($b.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){ Save $b.SelectedPath }
}
exit 0
'''

def _win_dialog(kind, title, filt=None):
    """用 Windows『現代檔案總管樣式』對話框選資料夾/檔案:資料夾用 IFileOpenDialog
    (FOS_PICKFOLDERS,有導覽窗格/位址列,非舊樹狀窗),檔案用 OpenFileDialog。
    .py 與打包 exe 一致,不依賴 tkinter。透過內建 powershell 執行,標題以參數傳入
    (走 Windows 寬字元,不受 .ps1 編碼影響)。
    回傳 (狀態, 路徑):'ok' 選了 / 'cancel' 取消 / 'unavailable' 叫不出視窗。"""
    import subprocess, tempfile
    fdo,tmp=tempfile.mkstemp(suffix=".txt"); os.close(fdo)
    fdp,ps1=tempfile.mkstemp(suffix=".ps1"); os.close(fdp)
    def _clean():
        for x in (tmp,ps1):
            try: os.remove(x)
            except Exception: pass
    try:
        with open(ps1,"w",encoding="utf-8") as fp: fp.write(_PS_PICKER)
    except Exception as e:
        _clean(); print("  (選擇視窗建立失敗:%s)"%e); return ("unavailable",None)
    args=["powershell","-NoProfile","-STA","-ExecutionPolicy","Bypass","-File",ps1,
          "-Title",title,"-Out",tmp,"-Mode",kind,"-Filter",(filt or "所有檔案|*.*")]
    try:
        subprocess.run(args,timeout=600)
    except Exception as e:
        _clean(); print("  (原生選擇視窗叫不出來:%s)"%e); return ("unavailable",None)
    path=None
    try:
        with open(tmp,encoding="utf-8-sig") as fp: path=fp.read().strip() or None
    except Exception: path=None
    _clean()
    return ("ok",path) if path else ("cancel",None)

def wizard():
    """選路徑一律用 Windows 原生『選擇資料夾/檔案』對話框(.py 與打包 exe 行為一致,不用拖拉)。"""
    def get_dir(name):
        st,p=_win_dialog("dir","選擇「%s」資料夾"%name)
        if st=="unavailable": return _ask_path("把『%s』資料夾拖進本視窗,按 Enter:"%name,"dir")
        return p
    def get_file(name,ft):
        filt="|".join("%s|%s"%(desc,pat) for desc,pat in ft) if ft else "所有檔案|*.*"
        st,p=_win_dialog("file","選擇「%s」"%name,filt)
        if st=="unavailable": return _ask_path("把『%s』拖進本視窗,按 Enter:"%name,"file")
        return p

    print("="*46); print("            空拍套疊工具"); print("="*46)
    print("[1] 只合成空拍(拼接 → mosaic.png,不套圖)")
    print("[2] 合成 + 產生對位工具(要套施工圖)")
    print("[3] 產出套疊圖(已在 picker.html 點好、下載 points.txt 後)")
    print("提示:選 1/2/3 後會彈出『選擇資料夾/檔案』視窗(若被黑視窗擋住,點工作列圖示)")
    ch=input("請選 1 / 2 / 3:").strip()
    if ch=="1":
        folder=get_dir("空拍照")
        if not folder: print("已結束"); return
        m=cmd_stitch(argparse.Namespace(input=folder))
        if m:
            print("\n★ 完成!合成檔:%s"%m)
            try: os.startfile(os.path.dirname(m))
            except Exception: pass
    elif ch=="2":
        folder=get_dir("空拍照")
        if not folder: print("已結束"); return
        mosaic=cmd_stitch(argparse.Namespace(input=folder))
        if not mosaic: return
        pdf=get_file("施工圖 PDF",[("PDF","*.pdf")])
        if not pdf: print("已完成合成,未套圖,結束"); return
        page=ask_pdf_page()
        cmd_prep(argparse.Namespace(mosaic=mosaic,pdf=pdf,page=page,fence_layer="施工圍籬"))
        picker=os.path.join(os.path.dirname(mosaic),"picker.html")
        try: os.startfile(picker)
        except Exception: pass
        print("\n★ 已開啟 picker.html:拖 4 個基地角 → 按【下載 points.txt】(存到同資料夾)")
        print("★ 完成後,再次執行本程式,選 [3] 產出套疊圖。")
    elif ch=="3":
        mosaic=get_file("mosaic.png",[("PNG","*.png")])
        if not mosaic: print("已結束"); return
        pdf=get_file("施工圖 PDF",[("PDF","*.pdf")])
        if not pdf: print("已結束"); return
        page=ask_pdf_page()
        points=get_file("points.txt",[("TXT","*.txt")])
        if not points: print("已結束"); return
        cmd_overlay(argparse.Namespace(mosaic=mosaic,pdf=pdf,points=points,page=page,
                    fence_layer="施工圍籬",layers=DEFAULT_LAYERS,no_whiteout=False))
        try: os.startfile(os.path.dirname(os.path.abspath(mosaic)))
        except Exception: pass
        print("\n★ 已輸出 套疊.png / 套疊_透明層.png(在 mosaic 所在資料夾)")
    else:
        print("未選擇,結束")

def main():
    if len(sys.argv)==1:   # 雙擊(無參數)→ 精靈模式
        try: wizard()
        except Exception as e:
            import traceback; print("錯誤:",e); traceback.print_exc()
        try: input("\n按 Enter 關閉…")
        except Exception: pass
        return
    ap=argparse.ArgumentParser(description="空拍套疊工具")
    sub=ap.add_subparsers(dest="cmd",required=True)
    s1=sub.add_parser("stitch"); s1.add_argument("input"); s1.add_argument("--all",action="store_true",help="即使全是傾斜照也強制拼接"); s1.set_defaults(func=cmd_stitch)
    s2=sub.add_parser("prep"); s2.add_argument("mosaic"); s2.add_argument("pdf"); s2.add_argument("--page",type=int,default=3); s2.add_argument("--fence-layer",default="施工圍籬"); s2.set_defaults(func=cmd_prep)
    s3=sub.add_parser("overlay"); s3.add_argument("mosaic"); s3.add_argument("pdf"); s3.add_argument("points"); s3.add_argument("--page",type=int,default=3); s3.add_argument("--fence-layer",default="施工圍籬"); s3.add_argument("--layers",default=DEFAULT_LAYERS); s3.add_argument("--no-whiteout",action="store_true"); s3.set_defaults(func=cmd_overlay)
    a=ap.parse_args(); a.func(a)

if __name__=="__main__":
    main()
