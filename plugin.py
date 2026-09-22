# -*- coding: utf-8 -*-
import os, math, csv, datetime, hashlib, json, time, platform, sys
import numpy as np
from osgeo import gdal

from qgis.PyQt.QtWidgets import (
    QDialog,QVBoxLayout,QFormLayout,QComboBox,QDoubleSpinBox,QSpinBox,
    QPushButton,QLabel,QFileDialog,QHBoxLayout,QMessageBox,QCheckBox,QTabWidget,
    QWidget,QTextBrowser,QProgressBar,QApplication
)
from qgis.core import (
    QgsProject,QgsRasterLayer,QgsVectorLayer,QgsField,QgsFeature,QgsGeometry,
    QgsPointXY,QgsVectorFileWriter,QgsCoordinateTransformContext,QgsLineSymbol,
    QgsSingleSymbolRenderer,Qgis
)
from .compat import QAction, exec_dialog, field_type

AZIMUTHS=[0,45,90,135,180,225,270,315]

REFS="""
<h2>GeoVisual Lineament v7 — Gestalt + LINE</h2>
<p><b>Sidiropoulou Velidou et al. (2015), Mathematical Geosciences 47:249–276</b>:
the image is brought to a coarser scale S&lt;1 by Gaussian smoothing (5×5, σ=0.8/S)
and sub-sampling. A 2×2 gradient is calculated, low gradient pixels are rejected by ω,
and 8-connected region growing groups pixels with similar gradient/level-line orientation
within angle tolerance τ. Each line-support region is approximated by its minimum oriented
rectangle/inertia axis and tested with a Helmholtz a-contrario false-alarm rate (FAR).
Segments with FAR&lt;ε are meaningful.</p>

<p><b>Salui (2018), Journal Geological Society of India 92:321–328</b>:
the PCI LINE workflow consists of edge detection, thresholding and curve extraction;
Canny non-maximum suppression/thinning is followed by minimum curve length, polyline fitting,
and linking of similarly oriented nearby endpoints (ATHR, DTHR). Hough is useful but may
produce globally collinear results, so v7 uses LINE-style local linking rather than global
scene-spanning Hough lines.</p>

<p><b>Multi-azimuth DEM practice</b>: eight illumination azimuths are used to reduce directional
illumination bias. v7 clusters repeated detections across azimuths and three nearby scales.</p>

<hr>
<p><b>Important:</b> v7 deliberately abandons the previous paired-edge heuristic.
It follows the published line-support-region method directly. The output is a remotely
sensed DEM lineament candidate, not automatically a fault.</p>
"""

class GeoVisualLineamentPlugin:
    def __init__(self,iface):
        self.iface=iface; self.action=None
    def initGui(self):
        self.action=QAction("GeoVisual Lineament — Experimental Validation v7.2.3",self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&Geology",self.action)
        self.iface.addToolBarIcon(self.action)
    def unload(self):
        if self.action:
            self.iface.removePluginMenu("&Geology",self.action)
            self.iface.removeToolBarIcon(self.action)
    def run(self):
        exec_dialog(LineamentDialog(self.iface))

class LineamentDialog(QDialog):
    def __init__(self,iface):
        super().__init__(iface.mainWindow())
        self.iface=iface; self.layers=[]; self.outdir=os.path.expanduser("~")
        self.setWindowTitle("GeoVisual Lineament v7.2.3 — Stability Build")
        self.resize(900,820)

        main=QVBoxLayout(self); tabs=QTabWidget(); main.addWidget(tabs)
        page=QWidget(); lay=QVBoxLayout(page); form=QFormLayout(); lay.addLayout(form)

        self.raster=QComboBox()
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr,QgsRasterLayer):
                self.layers.append(lyr); self.raster.addItem(lyr.name())
        form.addRow("Input clean DEM:",self.raster)

        self.alt=QDoubleSpinBox(); self.alt.setRange(5,85); self.alt.setValue(45); self.alt.setSuffix("°")
        form.addRow("Sun altitude:",self.alt)

        self.scale=QDoubleSpinBox(); self.scale.setRange(0.20,0.90); self.scale.setValue(0.30); self.scale.setSingleStep(0.05)
        form.addRow("Primary scale factor S:",self.scale)

        self.omega_pct=QDoubleSpinBox(); self.omega_pct.setRange(55,99); self.omega_pct.setValue(72); self.omega_pct.setSuffix("%")
        form.addRow("Gradient threshold ω (percentile):",self.omega_pct)

        self.tau=QDoubleSpinBox(); self.tau.setRange(10,30); self.tau.setValue(22.5); self.tau.setSuffix("°")
        form.addRow("Region-growing angle tolerance τ:",self.tau)

        self.epsilon=QDoubleSpinBox(); self.epsilon.setDecimals(3); self.epsilon.setRange(0.001,10.0); self.epsilon.setValue(1.0)
        form.addRow("Meaningfulness threshold ε (FAR):",self.epsilon)

        self.min_region=QSpinBox(); self.min_region.setRange(3,200); self.min_region.setValue(6)
        form.addRow("Minimum support-region pixels:",self.min_region)

        self.min_length=QSpinBox(); self.min_length.setRange(5,500); self.min_length.setValue(20)
        form.addRow("Minimum final segment length (scaled px):",self.min_length)

        self.athr=QDoubleSpinBox(); self.athr.setRange(3,30); self.athr.setValue(18); self.athr.setSuffix("°")
        form.addRow("LINE linking angle ATHR:",self.athr)

        self.dthr=QDoubleSpinBox(); self.dthr.setRange(5,60); self.dthr.setValue(18)
        form.addRow("LINE linking distance DTHR (px):",self.dthr)

        self.min_az=QSpinBox(); self.min_az.setRange(1,8); self.min_az.setValue(1)
        form.addRow("Minimum azimuth detections:",self.min_az)

        self.maxdim=QSpinBox(); self.maxdim.setRange(800,4000); self.maxdim.setValue(1800)
        form.addRow("Maximum pre-scale raster dimension:",self.maxdim)

        self.saveqc=QCheckBox("Save support/meaningfulness QC CSV")
        self.saveqc.setChecked(True); lay.addWidget(self.saveqc)

        self.validation=QCheckBox("Run A0–A5 CGER experimental validation build")
        self.validation.setChecked(True); lay.addWidget(self.validation)

        row=QHBoxLayout(); self.outlabel=QLabel("Output: timestamped run folder")
        choose=QPushButton("Choose folder"); choose.clicked.connect(self.choose_folder)
        row.addWidget(self.outlabel,1); row.addWidget(choose); lay.addLayout(row)

        self.progress=QProgressBar(); self.progress.setRange(0,100); lay.addWidget(self.progress)

        note=QLabel(
            "<b>Use default parameters first.</b> This version follows the published Gestalt line-support-region "
            "algorithm much more directly. It no longer requires paired edges and no longer uses unrestricted Hough lines."
        )
        note.setWordWrap(True); lay.addWidget(note)

        self.runbtn=QPushButton("RUN v7.2.3 STABLE EXTRACTION + A0–A5 VALIDATION"); self.runbtn.clicked.connect(self.process)
        lay.addWidget(self.runbtn)

        tabs.addTab(page,"Extraction")
        refs=QTextBrowser(); refs.setHtml(REFS); tabs.addTab(refs,"References & exact method")

    def choose_folder(self):
        d=QFileDialog.getExistingDirectory(self,"Output folder",self.outdir)
        if d: self.outdir=d; self.outlabel.setText(d)

    # ---------- image preparation ----------
    def _shift(self,a,dy,dx,fill=0):
        out=np.full_like(a,fill)
        rs0=max(0,-dy); rs1=a.shape[0]-max(0,dy)
        cs0=max(0,-dx); cs1=a.shape[1]-max(0,dx)
        rd0=max(0,dy); rd1=a.shape[0]-max(0,-dy)
        cd0=max(0,dx); cd1=a.shape[1]-max(0,-dx)
        out[rd0:rd1,cd0:cd1]=a[rs0:rs1,cs0:cs1]
        return out

    def _gaussian5(self,a,sigma):
        # exact 5-sample discrete Gaussian kernel, separable
        x=np.arange(-2,3,dtype=float)
        k=np.exp(-(x*x)/(2.0*max(sigma,1e-6)**2))
        k=k/k.sum()
        z=a.astype(np.float32)

        # horizontal
        tmp=np.zeros_like(z)
        norm=np.zeros_like(z)
        for i,dx in enumerate(range(-2,3)):
            valid=np.isfinite(z)
            tmp += self._shift(np.where(valid,z,0.0),0,dx,0.0)*k[i]
            norm += self._shift(valid.astype(np.float32),0,dx,0.0)*k[i]
        tmp=np.where(norm>0,tmp/np.maximum(norm,1e-12),np.nan)

        # vertical
        out=np.zeros_like(tmp)
        norm=np.zeros_like(tmp)
        for i,dy in enumerate(range(-2,3)):
            valid=np.isfinite(tmp)
            out += self._shift(np.where(valid,tmp,0.0),dy,0,0.0)*k[i]
            norm += self._shift(valid.astype(np.float32),dy,0,0.0)*k[i]
        return np.where(norm>0,out/np.maximum(norm,1e-12),np.nan)

    def _hillshade(self,z,px,py,az,alt):
        gy,gx=np.gradient(z,abs(py),abs(px))
        slope=np.arctan(np.hypot(gx,gy)); aspect=np.arctan2(-gx,gy)
        azr=np.deg2rad(az); altr=np.deg2rad(alt)
        return (np.sin(altr)*np.cos(slope)+np.cos(altr)*np.sin(slope)*np.cos(azr-aspect)).astype(np.float32)

    def _gradient2x2(self,img):
        # Equations used in Sidiropoulou Velidou et al. (2015)
        a=img[:-1,:-1]
        b=img[:-1,1:]
        c=img[1:,:-1]
        d=img[1:,1:]
        gx=(b+d-a-c)/2.0
        gy=(c+d-a-b)/2.0
        mag=np.sqrt(gx*gx+gy*gy).astype(np.float32)
        # paper uses phi = atan2(gx, -gy), retaining 0..360 direction
        phi=(np.rad2deg(np.arctan2(gx,-gy))+360.0)%360.0
        return mag,phi.astype(np.float32)

    def _circ_diff360(self,a,b):
        return abs(((a-b+180.0)%360.0)-180.0)

    def _circ_mean360(self,angles):
        aa=np.deg2rad(np.asarray(angles,float))
        return (math.degrees(math.atan2(np.sum(np.sin(aa)),np.sum(np.cos(aa))))+360.0)%360.0

    # ---------- exact region growing from strongest unused seed ----------
    def _regions(self,mag,phi,omega,tau,minpix):
        active=(mag>omega)&np.isfinite(mag)&np.isfinite(phi)
        used=np.zeros(active.shape,bool)
        regions=[]

        # process strong pixels descending, approximating Algorithm 1 seed=max(C)
        ys,xs=np.nonzero(active)
        if len(xs)==0:
            return regions
        order=np.argsort(mag[ys,xs])[::-1]
        nbr=[(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]

        for ind in order:
            sy=int(ys[ind]); sx=int(xs[ind])
            if used[sy,sx] or not active[sy,sx]:
                continue

            reg=[(sy,sx)]
            used[sy,sx]=True
            angles=[float(phi[sy,sx])]
            theta=float(phi[sy,sx])
            queue=[(sy,sx)]

            while queue:
                y,x=queue.pop()
                additions=[]
                for dy,dx in nbr:
                    yy=y+dy; xx=x+dx
                    if 0<=yy<active.shape[0] and 0<=xx<active.shape[1]:
                        if active[yy,xx] and not used[yy,xx]:
                            if self._circ_diff360(float(phi[yy,xx]),theta)<=tau:
                                used[yy,xx]=True
                                additions.append((yy,xx))
                if additions:
                    for q in additions:
                        reg.append(q); angles.append(float(phi[q[0],q[1]])); queue.append(q)
                    theta=self._circ_mean360(angles)

            if len(reg)>=minpix:
                regions.append((reg,theta))
        return regions

    # ---------- rectangle / inertia segment ----------
    def _region_segment(self,reg,theta,mag,phi,tau):
        rr=np.array([p[0] for p in reg],dtype=float)
        cc=np.array([p[1] for p in reg],dtype=float)
        weights=np.array([float(mag[p[0],p[1]]) for p in reg],dtype=float)
        sw=max(weights.sum(),1e-12)

        cx=float(np.sum(weights*cc)/sw)
        cy=float(np.sum(weights*rr)/sw)

        x=cc-cx; y=rr-cy
        mxx=float(np.sum(weights*x*x)/sw)
        myy=float(np.sum(weights*y*y)/sw)
        mxy=float(np.sum(weights*x*y)/sw)
        M=np.array([[mxx,mxy],[mxy,myy]],dtype=float)

        vals,vecs=np.linalg.eigh(M)
        # Paper: rectangle direction = first inertia axis associated with smallest eigenvalue.
        # For a line-support cloud, the long rectangle axis is perpendicular to the smallest-inertia axis.
        normal=vecs[:,np.argmin(vals)]
        major=np.array([-normal[1],normal[0]])
        minor=normal

        pts=np.column_stack([cc,rr])
        cen=np.array([cx,cy])
        q=pts-cen
        t=q@major
        u=q@minor

        tmin=float(t.min()); tmax=float(t.max())
        umin=float(u.min()); umax=float(u.max())
        length=tmax-tmin
        width=max(1.0,umax-umin)

        if length<=0:
            return None

        p1=cen+major*tmin
        p2=cen+major*tmax

        # Count aligned active pixels in the estimated rectangle for FAR.
        xmin=max(0,int(math.floor(min(p1[0],p2[0])-width-2)))
        xmax=min(mag.shape[1]-1,int(math.ceil(max(p1[0],p2[0])+width+2)))
        ymin=max(0,int(math.floor(min(p1[1],p2[1])-width-2)))
        ymax=min(mag.shape[0]-1,int(math.ceil(max(p1[1],p2[1])+width+2)))

        n=0; k=0
        thetaA=theta
        for yy in range(ymin,ymax+1):
            for xx in range(xmin,xmax+1):
                qq=np.array([xx-cx,yy-cy])
                tt=float(np.dot(qq,major))
                uu=float(np.dot(qq,minor))
                if tmin-0.5<=tt<=tmax+0.5 and umin-0.5<=uu<=umax+0.5:
                    n+=1
                    if self._circ_diff360(float(phi[yy,xx]),thetaA)<=tau:
                        k+=1

        if n<=0 or k<=0:
            return None

        az=(math.degrees(math.atan2(p2[0]-p1[0],-(p2[1]-p1[1])))+360.0)%180.0
        return {
            "p1":tuple(p1),"p2":tuple(p2),"length":float(length),"width":float(width),
            "az":az,"n":int(n),"k":int(k),"region_pixels":len(reg)
        }

    # ---------- Helmholtz FAR ----------
    def _log_binom_tail(self,n,k,p):
        if k<=0: return 0.0
        if k>n: return -1e30
        logs=[]
        for j in range(k,n+1):
            lj=math.lgamma(n+1)-math.lgamma(j+1)-math.lgamma(n-j+1)+j*math.log(p)+(n-j)*math.log(1-p)
            logs.append(lj)
        m=max(logs)
        return m+math.log(sum(math.exp(v-m) for v in logs))

    def _far(self,n,k,tau,N,M):
        p=max(1e-8,min(0.999999,float(tau)/180.0))
        # exact binomial tail for moderate rectangles; KL bound for very large ones
        if n<=600:
            logB=self._log_binom_tail(n,k,p)
        else:
            q=float(k)/float(n)
            if q<=p:
                return 1e99
            D=q*math.log(q/p)+(1-q)*math.log((1-q)/(1-p))
            logB=-n*D

        # Paper: (N*M)^(5/2) * B(n,k,p)
        logF=2.5*math.log(max(2.0,float(N*M)))+logB
        if logF>230:
            return 1e99
        return math.exp(logF)

    # ---------- cross-azimuth / cross-scale fusion ----------
    def _adiff(self,a,b):
        d=abs(a-b)%180.0
        return min(d,180.0-d)

    def _point_line_dist(self,p,a,b):
        vx=b[0]-a[0]; vy=b[1]-a[1]
        den=math.hypot(vx,vy)
        if den<=1e-9:
            return math.hypot(p[0]-a[0],p[1]-a[1])
        return abs(vy*p[0]-vx*p[1]+b[0]*a[1]-b[1]*a[0])/den

    def _match(self,a,b,dist=10.0,ang=20.0):
        if self._adiff(a["az"],b["az"])>ang:
            return False
        ma=((a["p1"][0]+a["p2"][0])/2.0,(a["p1"][1]+a["p2"][1])/2.0)
        mb=((b["p1"][0]+b["p2"][0])/2.0,(b["p1"][1]+b["p2"][1])/2.0)
        lateral=max(
            self._point_line_dist(ma,b["p1"],b["p2"]),
            self._point_line_dist(mb,a["p1"],a["p2"])
        )
        center=math.hypot(ma[0]-mb[0],ma[1]-mb[1])
        reach=0.7*(a["length"]+b["length"])+dist
        return lateral<=dist and center<=reach

    def _fuse(self,recs):
        out=[]
        for r in sorted(recs,key=lambda x:x["length"],reverse=True):
            hit=None
            for q in out:
                if self._match(r,q,10.0,20.0):
                    hit=q
                    break
            if hit is None:
                q=dict(r)
                q["az_hits"]={r["az_id"]}
                q["scale_hits"]={r["scale_id"]}
                q["detections"]=1
                out.append(q)
            else:
                hit["az_hits"].add(r["az_id"])
                hit["scale_hits"].add(r["scale_id"])
                hit["detections"]+=1
                hit["far"]=min(hit["far"],r["far"])
                hit["region_pixels"]=max(hit["region_pixels"],r["region_pixels"])
                if r["length"]>hit["length"]:
                    for key in ("p1","p2","length","width","az","source"):
                        hit[key]=r.get(key,hit.get(key))
        for q in out:
            q["az_support"]=len(q["az_hits"])
            q["scale_support"]=len(q["scale_hits"])
        return out

    # ---------- continuity / isolation refinement ----------
    def _segment_mid(self,r):
        return (
            (r["p1"][0]+r["p2"][0])/2.0,
            (r["p1"][1]+r["p2"][1])/2.0
        )

    def _continuity_score(self,r,recs,search_dist=35.0,angle_tol=18.0):
        """
        Local continuity score in [0,1].
        Rewards nearby segments occupying the same corridor and orientation family.
        This is used only as a refinement score; it does not replace FAR meaningfulness.
        """
        m=self._segment_mid(r)
        hits=0
        tested=0
        for q in recs:
            if q is r:
                continue
            mq=self._segment_mid(q)
            d=math.hypot(m[0]-mq[0],m[1]-mq[1])
            if d>search_dist:
                continue
            tested += 1
            if self._adiff(r["az"],q["az"])<=angle_tol:
                lateral=max(
                    self._point_line_dist(m,q["p1"],q["p2"]),
                    self._point_line_dist(mq,r["p1"],r["p2"])
                )
                if lateral<=max(8.0,search_dist*0.30):
                    hits += 1
        if tested==0:
            return 0.0
        return min(1.0, hits/3.0)

    def _isolated_short(self,r,recs,min_len,search_dist=30.0):
        """
        Remove only short isolated fragments.
        Long meaningful segments are never removed here.
        """
        if r["length"] >= 1.35*min_len:
            return False

        m=self._segment_mid(r)
        for q in recs:
            if q is r:
                continue
            mq=self._segment_mid(q)
            d=math.hypot(m[0]-mq[0],m[1]-mq[1])
            if d<=search_dist and self._adiff(r["az"],q["az"])<=22.5:
                return False

        return (
            r.get("az_support",1)<=1 and
            r.get("scale_support",1)<=1 and
            r.get("far",1.0)>0.20
        )

    # ---------- LINE-style local linking ----------
    def _endpoint_gap(self,a,b):
        pairs=[
            (a["p1"],b["p1"],0,0),(a["p1"],b["p2"],0,1),
            (a["p2"],b["p1"],1,0),(a["p2"],b["p2"],1,1)]
        best=None
        for p,q,ia,ib in pairs:
            d=math.hypot(p[0]-q[0],p[1]-q[1])
            if best is None or d<best[0]:
                best=(d,ia,ib)
        return best

    def _link(self,recs,athr,dthr):
        work=[dict(r) for r in recs]
        cell=max(5.0,float(dthr))

        for _ in range(5):
            idx={}
            for i,r in enumerate(work):
                for p in (r["p1"],r["p2"]):
                    k=(int(math.floor(p[0]/cell)),int(math.floor(p[1]/cell)))
                    idx.setdefault(k,set()).add(i)

            used=set(); out=[]; changed=False
            for i,a in enumerate(work):
                if i in used: continue
                cand=set()
                for p in (a["p1"],a["p2"]):
                    kx=int(math.floor(p[0]/cell)); ky=int(math.floor(p[1]/cell))
                    for dx in (-1,0,1):
                        for dy in (-1,0,1):
                            cand.update(idx.get((kx+dx,ky+dy),()))
                cand.discard(i)

                best=None
                for j in cand:
                    if j in used: continue
                    b=work[j]
                    if self._adiff(a["az"],b["az"])>athr: continue
                    gap,ia,ib=self._endpoint_gap(a,b)
                    if gap>dthr: continue

                    ma=((a["p1"][0]+a["p2"][0])/2.0,(a["p1"][1]+a["p2"][1])/2.0)
                    mb=((b["p1"][0]+b["p2"][0])/2.0,(b["p1"][1]+b["p2"][1])/2.0)
                    lateral=max(
                        self._point_line_dist(ma,b["p1"],b["p2"]),
                        self._point_line_dist(mb,a["p1"],a["p2"])
                    )
                    if lateral>max(8.0,dthr*0.6): continue

                    merit=(1-gap/max(dthr,1e-9))*0.55 + \
                          (1-self._adiff(a["az"],b["az"])/max(athr,1e-9))*0.30 + \
                          (1-min(1.0,lateral/max(8.0,dthr*0.6)))*0.15
                    if best is None or merit>best[0]:
                        best=(merit,j)

                if best is None:
                    used.add(i); out.append(a); continue

                _,j=best
                b=work[j]
                pts=np.array([a["p1"],a["p2"],b["p1"],b["p2"]],float)
                cen=pts.mean(0); q=pts-cen
                vals,vecs=np.linalg.eigh(np.cov(q,rowvar=False)); v=vecs[:,np.argmax(vals)]
                t=q@v
                p1=tuple(cen+v*t.min()); p2=tuple(cen+v*t.max())
                L=math.hypot(p2[0]-p1[0],p2[1]-p1[1])
                az=(math.degrees(math.atan2(p2[0]-p1[0],-(p2[1]-p1[1])))+360)%180

                m=dict(a)
                m.update({"p1":p1,"p2":p2,"length":L,"az":az,
                          "far":min(a["far"],b["far"]),
                          "az_support":max(a.get("az_support",1),b.get("az_support",1)),
                          "scale_support":max(a.get("scale_support",1),b.get("scale_support",1)),
                          "detections":a.get("detections",1)+b.get("detections",1),
                          "region_pixels":max(a.get("region_pixels",0),b.get("region_pixels",0)),
                          "width":max(a.get("width",0.0),b.get("width",0.0)),
                          "source":"LINKED"})
                used.add(i); used.add(j); out.append(m); changed=True

            work=out
            if not changed: break
        return work

    def _pix_to_map(self,p,gt,step):
        # step may be scalar (legacy) or (x_step, y_step) for direct GDAL downsample reads.
        if isinstance(step,(tuple,list)):
            sx,sy=float(step[0]),float(step[1])
        else:
            sx=sy=float(step)
        c=p[0]*sx; r=p[1]*sy
        return (gt[0]+(c+0.5*sx)*gt[1]+(r+0.5*sy)*gt[2],
                gt[3]+(c+0.5*sx)*gt[4]+(r+0.5*sy)*gt[5])

    def _write_vectors(self,path,name,recs,crs,gt,step,add_to_project=True):
        if os.path.exists(path): os.remove(path)
        mem=QgsVectorLayer("LineString?crs="+crs.authid(),name+"_mem","memory")
        pr=mem.dataProvider()
        pr.addAttributes([
            QgsField("ID",field_type("int")),QgsField("LENGTH",field_type("double")),
            QgsField("AZIMUTH",field_type("double")),QgsField("WIDTH_PX",field_type("double")),
            QgsField("FAR",field_type("double")),QgsField("AZ_SUPPORT",field_type("int")),
            QgsField("SCALE_SUPP",field_type("int")),QgsField("DETECTIONS",field_type("int")),
            QgsField("REG_PIX",field_type("int")),QgsField("CONTINUITY",field_type("double")),
            QgsField("SCORE",field_type("double")),QgsField("SOURCE",field_type("string"),"",16),
            QgsField("QUALITY",field_type("string"),"",12),QgsField("STATUS",field_type("string"),"",28)
        ])
        mem.updateFields()

        feats=[]
        for i,r in enumerate(recs,1):
            p1=self._pix_to_map(r["p1"],gt,step); p2=self._pix_to_map(r["p2"],gt,step)
            f=QgsFeature(mem.fields())
            f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p1),QgsPointXY(*p2)]))

            far_score=max(0.0,min(1.0,-math.log10(max(r["far"],1e-30))/10.0))
            continuity=float(r.get("continuity",0.0))
            score=(
                0.24*min(1.0,r.get("az_support",1)/3.0) +
                0.20*min(1.0,r.get("scale_support",1)/2.0) +
                0.24*far_score +
                0.17*min(1.0,r["length"]/60.0) +
                0.15*continuity
            )
            q="HIGH" if score>=0.68 else ("MEDIUM" if score>=0.49 else "LOW")

            f.setAttributes([
                i,f.geometry().length(),r["az"],r.get("width",0.0),r["far"],
                r.get("az_support",1),r.get("scale_support",1),r.get("detections",1),
                r.get("region_pixels",0),continuity,score,r.get("source","REGION"),q,
                "DEM_LINEAMENT_CANDIDATE"
            ])
            feats.append(f)

        pr.addFeatures(feats); mem.updateExtents()
        opt=QgsVectorFileWriter.SaveVectorOptions()
        opt.driverName="GPKG"; opt.layerName=name
        opt.actionOnExistingFile=QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        res=QgsVectorFileWriter.writeAsVectorFormatV3(mem,path,QgsCoordinateTransformContext(),opt)
        if res[0]!=QgsVectorFileWriter.WriterError.NoError:
            raise Exception("Vector write error: "+str(res[1]))

        if add_to_project:
            vl=QgsVectorLayer(path+"|layername="+name,name,"ogr")
            if vl.isValid():
                if name=="Principal_Lineaments":
                    props={"width":"0.9","line_color":"220,30,30"}
                elif name=="Selected_Lineaments":
                    props={"width":"0.65","line_color":"255,140,0"}
                else:
                    props={"width":"0.35","line_color":"90,90,90"}
                vl.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(props)))
                QgsProject.instance().addMapLayer(vl)
                node=QgsProject.instance().layerTreeRoot().findLayer(vl.id())
                if node is not None and name=="All_Meaningful_Segments":
                    node.setItemVisibilityChecked(False)

    def _stats(self,path,recs):
        bins={i:0 for i in range(0,180,10)}
        for r in recs:
            bins[int((r["az"]%180)//10)*10]+=1
        with open(path,"w",newline="",encoding="utf-8") as f:
            w=csv.writer(f); w.writerow(["BIN_START","BIN_END","COUNT"])
            for b in sorted(bins): w.writerow([b,b+10,bins[b]])


    # ---------- v7.2 experimental validation helpers ----------
    def _sha256(self,path):
        h=hashlib.sha256()
        with open(path,"rb") as f:
            for chunk in iter(lambda:f.read(1024*1024),b""):
                h.update(chunk)
        return h.hexdigest()

    def _continuity_evidence(self,r,recs,search_dist,angle_tol):
        m=self._segment_mid(r); hits=0; tested=0
        lateral_limit=max(8.0,search_dist*0.30)
        for q in recs:
            if q is r: continue
            mq=self._segment_mid(q)
            d=math.hypot(m[0]-mq[0],m[1]-mq[1])
            if d>search_dist: continue
            tested += 1
            if self._adiff(r["az"],q["az"])<=angle_tol:
                lateral=max(self._point_line_dist(m,q["p1"],q["p2"]),self._point_line_dist(mq,r["p1"],r["p2"]))
                if lateral<=lateral_limit: hits += 1
        return hits,tested,min(1.0,hits/3.0)

    def _is_locally_isolated(self,r,recs,search_dist,angle_tol=22.5):
        m=self._segment_mid(r)
        for q in recs:
            if q is r: continue
            mq=self._segment_mid(q)
            if math.hypot(m[0]-mq[0],m[1]-mq[1])<=search_dist and self._adiff(r["az"],q["az"])<=angle_tol:
                return False
        return True

    def _arm_keep(self,r,arm,min_len):
        protection=1.35*min_len
        short_protected=r["length"] < protection
        isolated=bool(r.get("is_isolated",False))
        weak_support=(r.get("az_support",1)<=1 and r.get("scale_support",1)<=1)
        weak_far=(r.get("far",1.0)>0.20)
        if arm=="A0": return True,"NO_CGER"
        if arm=="A1":
            delete=short_protected and isolated and weak_support and weak_far
            return (not delete),("DELETE_ALL_RESCUE_FAILED" if delete else "KEEP_CGER")
        if arm=="A2":
            delete=short_protected and weak_support and weak_far
            return (not delete),("DELETE_NO_CONTINUITY_RESCUE" if delete else "KEEP")
        if arm=="A3":
            delete=short_protected and isolated and weak_far
            return (not delete),("DELETE_NO_AZ_SCALE_RESCUE" if delete else "KEEP")
        if arm=="A4":
            delete=short_protected and isolated and weak_support
            return (not delete),("DELETE_NO_FAR_RESCUE" if delete else "KEEP")
        if arm=="A5":
            keep=r["length"]>=min_len
            return keep,("KEEP_MIN_LENGTH" if keep else "DELETE_MIN_LENGTH")
        return True,"UNKNOWN_ARM"

    def _validation_metrics(self,common,arm_recs,arm,runtime_s,min_len):
        protection=1.35*min_len
        short=[r for r in common if r["length"]<protection]
        isolated=[r for r in short if r.get("is_isolated",False)]
        coherent=[r for r in short if not r.get("is_isolated",False)]
        kept=set(r["candidate_id"] for r in arm_recs)
        iso_ret=sum(1 for r in isolated if r["candidate_id"] in kept)
        coh_ret=sum(1 for r in coherent if r["candidate_id"] in kept)
        return {
          "arm":arm,"n_entering":len(common),"n_retained":len(arm_recs),"n_deleted":len(common)-len(arm_recs),
          "n_short_lt_protection":len(short),"n_isolated_short":len(isolated),"n_coherent_short":len(coherent),
          "isolated_short_retained":iso_ret,"coherent_short_retained":coh_ret,
          "IFR":(iso_ret/len(short) if short else 0.0),
          "IFR_isolated_denominator":(iso_ret/len(isolated) if isolated else 0.0),
          "CSR":(coh_ret/len(coherent) if coherent else 0.0),
          "runtime_seconds":runtime_s
        }

    def _write_validation_package(self,outdir,common,arms,metrics,crs,gt,perf,input_path,params):
        # Common intermediate and all arm vectors; only A1 is added to map to avoid clutter.
        self._write_vectors(os.path.join(outdir,"common_intermediate_candidates.gpkg"),"Common_Intermediate_Candidates",common,crs,gt,perf,False)
        for arm,recs in arms.items():
            self._write_vectors(os.path.join(outdir,arm+"_candidates.gpkg"),arm+"_Candidates",recs,crs,gt,perf,arm=="A1")

        audit_path=os.path.join(outdir,"candidate_decision_audit.csv")
        with open(audit_path,"w",newline="",encoding="utf-8") as f:
            fields=["candidate_id","length_px","azimuth","az_support","scale_support","far","continuity_hits","continuity_tested","continuity_score","is_isolated","protected_length"]
            for a in ["A0","A1","A2","A3","A4","A5"]: fields += [a+"_keep",a+"_reason"]
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
            for r in common:
                row={k:r.get(k,"") for k in fields}
                row.update({"candidate_id":r["candidate_id"],"length_px":r["length"],"azimuth":r["az"],"az_support":r.get("az_support",1),"scale_support":r.get("scale_support",1),"far":r["far"],"continuity_hits":r.get("continuity_hits",0),"continuity_tested":r.get("continuity_tested",0),"continuity_score":r.get("continuity",0.0),"is_isolated":int(r.get("is_isolated",False)),"protected_length":int(r["length"]>=1.35*params["min_length"])})
                for a in ["A0","A1","A2","A3","A4","A5"]:
                    keep,reason=self._arm_keep(r,a,params["min_length"]); row[a+"_keep"]=int(keep); row[a+"_reason"]=reason
                w.writerow(row)

        with open(os.path.join(outdir,"A0-A5_metrics.csv"),"w",newline="",encoding="utf-8") as f:
            fields=list(metrics[0].keys())+["precision","recall","F1","fragmentation_index","matched_length_ratio"]
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
            for m in metrics:
                q=dict(m); q.update({"precision":"NA_REFERENCE_REQUIRED","recall":"NA_REFERENCE_REQUIRED","F1":"NA_REFERENCE_REQUIRED","fragmentation_index":"NA_REFERENCE_REQUIRED","matched_length_ratio":"NA_REFERENCE_REQUIRED"}); w.writerow(q)

        manifest={"build":"GeoVisual Lineament v7.2.3 Stability Build","timestamp":datetime.datetime.now().isoformat(),"input":input_path,"input_sha256":self._sha256(input_path),"qgis_version":getattr(Qgis,"QGIS_VERSION","unknown"),"python":sys.version,"platform":platform.platform(),"parameters":params,"arms":{a:len(v) for a,v in arms.items()},"notes":["All A0-A5 arms use the same common intermediate candidate set.","Precision/recall/F1/FI require an independent reference and are intentionally not fabricated.","A1 reproduces v7.1 CGER short-fragment decision logic."]}
        try: manifest["plugin_sha256"]=self._sha256(__file__)
        except Exception: manifest["plugin_sha256"]="unavailable"
        with open(os.path.join(outdir,"experiment_manifest.json"),"w",encoding="utf-8") as f: json.dump(manifest,f,indent=2,ensure_ascii=False)

    def process(self):
        # v7.2.3 stability build: direct GDAL downsample read avoids loading the full DEM
        # into RAM before the performance cap is applied. Heavy scientific logic is unchanged.
        self.runbtn.setEnabled(False)
        try:
            if not self.layers: raise Exception("No raster loaded.")
            lyr=self.layers[self.raster.currentIndex()]
            src=lyr.source().split("|")[0]
            ds=gdal.Open(src,gdal.GA_ReadOnly)
            if ds is None: raise Exception("GDAL cannot open DEM.")

            band=ds.GetRasterBand(1)
            w=int(ds.RasterXSize); h=int(ds.RasterYSize)
            maxdim=max(800,int(self.maxdim.value()))
            perf=max(1,int(math.ceil(max(h,w)/float(maxdim))))
            bw=max(1,int(math.ceil(w/float(perf))))
            bh=max(1,int(math.ceil(h/float(perf))))

            # Read already resampled. This is the key RAM-stability change in v7.2.3.
            z=band.ReadAsArray(0,0,w,h,buf_xsize=bw,buf_ysize=bh,buf_type=gdal.GDT_Float32)
            if z is None: raise Exception("GDAL failed to read DEM.")
            z=np.asarray(z,dtype=np.float32)
            nd=band.GetNoDataValue()
            valid=np.isfinite(z)
            if nd is not None: valid &= ~np.isclose(z,float(nd),rtol=0.0,atol=1e-6)
            if int(valid.sum())<100: raise Exception("Too few valid DEM pixels.")

            gt=ds.GetGeoTransform(); px=abs(gt[1]); py=abs(gt[5])
            if lyr.crs().isGeographic():
                QMessageBox.warning(self,"CRS warning","Use projected CRS before interpreting lengths.")

            fill=float(np.nanmedian(z[valid])); z[~valid]=fill
            # Exact source-pixel equivalent per processing pixel. Kept separately in X/Y.
            step=(w/float(bw),h/float(bh))
            bpx=px*step[0]; bpy=py*step[1]

            # Guardrail against pathological memory pressure even if the user raises maxdim.
            est_mb=(z.size*4*12)/(1024.0*1024.0)
            if est_mb>950:
                raise Exception("Estimated working memory is too high (%.0f MB). Lower Maximum pre-scale raster dimension." % est_mb)

            S0=self.scale.value()
            scales=sorted(set([max(0.20,S0-0.05),S0,min(0.90,S0+0.10)]))
            detections=[]
            total=len(scales)*len(AZIMUTHS)
            count=0
            MAX_DETECTIONS=150000
            MAX_REGIONS_PER_PASS=40000

            for sid,S in enumerate(scales):
                sigma=0.8/max(S,1e-6)
                sm=self._gaussian5(z,sigma)

                sf=max(1,int(round(1.0/max(S,1e-6))))
                zz=sm[::sf,::sf]
                spx=bpx*sf; spy=bpy*sf

                for aid,az in enumerate(AZIMUTHS):
                    count+=1
                    self.progress.setValue(int(5+70*count/float(total)))
                    QApplication.processEvents()

                    hs=self._hillshade(zz,spx,spy,az,self.alt.value())
                    mag,phi=self._gradient2x2(hs)
                    vals=mag[np.isfinite(mag)]
                    if vals.size<20: continue
                    omega=float(np.percentile(vals,self.omega_pct.value()))

                    regs=self._regions(mag,phi,omega,self.tau.value(),self.min_region.value())
                    if len(regs)>MAX_REGIONS_PER_PASS:
                        regs=sorted(regs,key=lambda x:len(x[0]),reverse=True)[:MAX_REGIONS_PER_PASS]

                    for reg,theta in regs:
                        s=self._region_segment(reg,theta,mag,phi,self.tau.value())
                        if s is None: continue
                        far=self._far(s["n"],s["k"],self.tau.value(),mag.shape[0],mag.shape[1])
                        if far>self.epsilon.value(): continue
                        s["p1"]=(s["p1"][0]*sf,s["p1"][1]*sf)
                        s["p2"]=(s["p2"][0]*sf,s["p2"][1]*sf)
                        s["length"]*=sf; s["width"]*=sf
                        s["far"]=far; s["az_id"]=aid; s["scale_id"]=sid; s["source"]="REGION"
                        detections.append(s)
                        if len(detections)>=MAX_DETECTIONS:
                            break
                    if len(detections)>=MAX_DETECTIONS:
                        break
                    del hs,mag,phi,vals,regs
                del zz,sm
                if len(detections)>=MAX_DETECTIONS:
                    break

            self.progress.setValue(80); QApplication.processEvents()
            fused=self._fuse(detections)
            linked=self._link(fused,self.athr.value(),self.dthr.value())

            common=[r for r in linked if r["length"]>=0.70*self.min_length.value()]
            common=sorted(common,key=lambda r:(round(self._segment_mid(r)[1],6),round(self._segment_mid(r)[0],6),round(r["az"],6),round(r["length"],6)))
            cdist=max(28.0,self.dthr.value()*1.8); idist=max(26.0,self.dthr.value()*1.6)
            for cid,r in enumerate(common,1):
                r["candidate_id"]=cid
                hits,tested,score=self._continuity_evidence(r,common,cdist,self.athr.value())
                r["continuity_hits"]=hits; r["continuity_tested"]=tested; r["continuity"]=score
                r["is_isolated"]=self._is_locally_isolated(r,common,idist,22.5)
                if cid%500==0: QApplication.processEvents()

            arms={}; arm_metrics=[]
            for arm in ["A0","A1","A2","A3","A4","A5"]:
                t0=time.perf_counter(); kept=[]
                for r in common:
                    keep,reason=self._arm_keep(r,arm,self.min_length.value())
                    if keep:
                        q=dict(r); q["arm_reason"]=reason; kept.append(q)
                runtime=time.perf_counter()-t0; arms[arm]=kept
                arm_metrics.append(self._validation_metrics(common,kept,arm,runtime,self.min_length.value()))

            all_segments=arms["A1"]
            selected=[r for r in all_segments if r["length"]>=self.min_length.value() and (r.get("az_support",1)>=self.min_az.value() or r.get("scale_support",1)>=2 or r["far"]<0.10 or r.get("continuity",0.0)>=0.34)]
            principal=[r for r in selected if r["length"]>=1.30*self.min_length.value() and r["far"]<1.0 and (r.get("az_support",1)>=2 or r.get("scale_support",1)>=2 or r.get("continuity",0.0)>=0.67)]
            selected_minaz2=[r for r in all_segments if r["length"]>=self.min_length.value() and (r.get("az_support",1)>=2 or r.get("scale_support",1)>=2 or r["far"]<0.10 or r.get("continuity",0.0)>=0.34)]

            self.progress.setValue(90); QApplication.processEvents()
            stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            outdir=os.path.join(self.outdir,"GeoVisual_v723_STABLE_"+stamp); os.makedirs(outdir,exist_ok=True)
            self._write_vectors(os.path.join(outdir,"All_Meaningful_Segments.gpkg"),"All_Meaningful_Segments",all_segments,lyr.crs(),gt,step)
            self._write_vectors(os.path.join(outdir,"Selected_Lineaments.gpkg"),"Selected_Lineaments",selected,lyr.crs(),gt,step)
            self._write_vectors(os.path.join(outdir,"Principal_Lineaments.gpkg"),"Principal_Lineaments",principal,lyr.crs(),gt,step)
            self._stats(os.path.join(outdir,"orientation_statistics.csv"),selected)
            self._write_vectors(os.path.join(outdir,"Selected_MINAZ2_Sensitivity.gpkg"),"Selected_MINAZ2_Sensitivity",selected_minaz2,lyr.crs(),gt,step,False)

            params={"sun_altitude":self.alt.value(),"primary_scale":S0,"scales":scales,"omega_percentile":self.omega_pct.value(),"tau":self.tau.value(),"epsilon_far":self.epsilon.value(),"min_region":self.min_region.value(),"min_length":self.min_length.value(),"athr":self.athr.value(),"dthr":self.dthr.value(),"min_az_selected":self.min_az.value(),"maxdim":self.maxdim.value(),"read_step_x":step[0],"read_step_y":step[1],"estimated_working_mb":round(est_mb,2),"detection_guard":MAX_DETECTIONS,"region_guard_per_pass":MAX_REGIONS_PER_PASS,"protection_multiplier":1.35,"weak_far_threshold":0.20,"continuity_selected_threshold":0.34,"continuity_principal_threshold":0.67}
            if self.validation.isChecked():
                self._write_validation_package(outdir,common,arms,arm_metrics,lyr.crs(),gt,step,src,params)

            with open(os.path.join(outdir,"METHOD_REFERENCES.txt"),"w",encoding="utf-8") as f:
                f.write("GeoVisual Lineament v7.2.3 — Stability Build\n\n")
                f.write("Scientific extraction and A0-A5 validation logic is retained from v7.2.1.\n")
                f.write("Stability changes: direct GDAL downsample read, RAM guard, candidate/region guards, and UI event yielding.\n")
                f.write("Sidiropoulou Velidou et al. (2015): Gaussian 5x5 sigma=0.8/S, scale S, 2x2 gradient, orientation region-growing, inertia rectangle, Helmholtz FAR epsilon-meaningful segments.\n")
                f.write("Salui (2018): LINE edge/threshold/curve workflow and ATHR/DTHR local linking.\n")
                f.write("Eight hillshade azimuths are used to reduce illumination bias. Output remains DEM-derived lineament candidates, not proven faults.\n")

            if self.saveqc.isChecked():
                with open(os.path.join(outdir,"detection_summary.csv"),"w",newline="",encoding="utf-8") as f:
                    wr=csv.writer(f); wr.writerow(["raw_meaningful_detections","common_intermediate","A1_CGER_refined","selected","principal","selected_minaz2_sensitivity","read_rows","read_cols","source_rows","source_cols"]); wr.writerow([len(detections),len(common),len(all_segments),len(selected),len(principal),len(selected_minaz2),bh,bw,h,w])

            self.progress.setValue(100)
            QMessageBox.information(self,"Finished","GeoVisual v7.2.3 stability run completed.\n\nMeaningful detections: %d\nA1 CGER refined: %d\nSelected: %d\nPrincipal: %d\n\nOutput: %s" % (len(detections),len(all_segments),len(selected),len(principal),outdir))

        except Exception as e:
            self.progress.setValue(0)
            QMessageBox.critical(self,"GeoVisual Lineament v7.2.3",str(e))
        finally:
            self.runbtn.setEnabled(True)

