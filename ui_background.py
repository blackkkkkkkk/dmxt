"""内容区背景层与画布式页面布局。

Tk 的 Frame 无法做到透明，所以页面内容不再塞进一个铺满的 Frame，而是以
canvas window item 的形式直接浮在背景之上——卡片之间自然露出背景图，
得到"卡片浮在壁纸上"的效果。

图片解码走 Windows GDI+（ctypes 调用 gdiplus.dll），因此 JPG/BMP/WebP
都能用，也不必为一张壁纸给 PyInstaller 增加 Pillow 依赖。
"""

import ctypes
import os
import sys
import tempfile
import tkinter as tk

from ui_theme import COLORS, font

# ---------------------------------------------------------------- 预设配色
# (key, 名称, 顶部色, 底部色, 是否深色)
PRESETS = [
    ("slate", "浅灰", "#F5F7FA", "#E6EBF2", False),
    ("ocean", "雾蓝", "#EAF3FF", "#D7E6FA", False),
    ("sand", "暖砂", "#FDF8EF", "#F3E6D3", False),
    ("mint", "青岚", "#EDFCF5", "#D6F1E4", False),
    ("violet", "暮紫", "#F7F4FF", "#E7E0FA", False),
    ("night", "墨夜", "#253244", "#131C29", True),
]

PRESET_MAP = {p[0]: {"key": p[0], "name": p[1], "top": p[2], "bottom": p[3], "dark": p[4]}
              for p in PRESETS}

DEFAULT_CONFIG = {
    "type": "preset",      # preset | image
    "preset": "slate",
    "image": "",
    "overlay": 15,          # 图片遮罩强度百分比：0 / 15 / 30 / 55
}


# ---------------------------------------------------------------- 颜色工具
def _to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _to_hex(channels):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in channels)


def lerp(c1, c2, t):
    """两个十六进制颜色按 t 线性插值。"""
    a, b = _to_rgb(c1), _to_rgb(c2)
    return _to_hex(a[i] + (b[i] - a[i]) * t for i in range(3))


# ---------------------------------------------------------------- GDI+ 解码
_PF_32BPP_ARGB = 0x26200A
_UNIT_PIXEL = 2

_gdiplus_token = None
_gp = None


class _StartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint32),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", ctypes.c_int),
        ("SuppressExternalCodecs", ctypes.c_int),
    ]


class _GpRect(ctypes.Structure):
    _fields_ = [("X", ctypes.c_int), ("Y", ctypes.c_int),
                ("Width", ctypes.c_int), ("Height", ctypes.c_int)]


class _BitmapData(ctypes.Structure):
    _fields_ = [
        ("Width", ctypes.c_uint), ("Height", ctypes.c_uint),
        ("Stride", ctypes.c_int), ("PixelFormat", ctypes.c_int),
        ("Scan0", ctypes.c_void_p), ("Reserved", ctypes.c_void_p),
    ]


def _gdiplus():
    """懒加载 gdiplus.dll 并完成 GdiplusStartup（每进程一次）。"""
    global _gp, _gdiplus_token
    if _gp is not None:
        return _gp
    if sys.platform != "win32":
        raise OSError("GDI+ 仅 Windows 可用")

    gp = ctypes.WinDLL("gdiplus.dll")
    vp, ci = ctypes.c_void_p, ctypes.c_int
    gp.GdiplusStartup.argtypes = [ctypes.POINTER(ctypes.c_size_t),
                                  ctypes.POINTER(_StartupInput), ctypes.c_void_p]
    gp.GdiplusStartup.restype = ci
    gp.GdipCreateBitmapFromFile.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(vp)]
    gp.GdipCreateBitmapFromFile.restype = ci
    gp.GdipGetImageWidth.argtypes = [vp, ctypes.POINTER(ctypes.c_uint)]
    gp.GdipGetImageWidth.restype = ci
    gp.GdipGetImageHeight.argtypes = [vp, ctypes.POINTER(ctypes.c_uint)]
    gp.GdipGetImageHeight.restype = ci
    gp.GdipCreateBitmapFromScan0.argtypes = [ci, ci, ci, ci, vp, ctypes.POINTER(vp)]
    gp.GdipCreateBitmapFromScan0.restype = ci
    gp.GdipGetImageGraphicsContext.argtypes = [vp, ctypes.POINTER(vp)]
    gp.GdipGetImageGraphicsContext.restype = ci
    gp.GdipSetInterpolationMode.argtypes = [vp, ci]
    gp.GdipSetInterpolationMode.restype = ci
    gp.GdipSetPixelOffsetMode.argtypes = [vp, ci]
    gp.GdipSetPixelOffsetMode.restype = ci
    gp.GdipSetSmoothingMode.argtypes = [vp, ci]
    gp.GdipSetSmoothingMode.restype = ci
    # 参数：graphics, image, dx, dy, dw, dh, sx, sy, sw, sh, unit, attach, cb, cbData
    gp.GdipDrawImageRectRectI.argtypes = [vp, vp] + [ci] * 9 + [vp, vp, vp]
    gp.GdipDrawImageRectRectI.restype = ci
    gp.GdipCreateSolidFill.argtypes = [ctypes.c_uint, ctypes.POINTER(vp)]
    gp.GdipCreateSolidFill.restype = ci
    gp.GdipFillRectangleI.argtypes = [vp, vp] + [ci] * 4
    gp.GdipFillRectangleI.restype = ci
    gp.GdipDeleteBrush.argtypes = [vp]
    gp.GdipDeleteBrush.restype = ci
    gp.GdipBitmapLockBits.argtypes = [vp, ctypes.POINTER(_GpRect), ci, ci,
                                      ctypes.POINTER(_BitmapData)]
    gp.GdipBitmapLockBits.restype = ci
    gp.GdipBitmapUnlockBits.argtypes = [vp, ctypes.POINTER(_BitmapData)]
    gp.GdipBitmapUnlockBits.restype = ci
    gp.GdipDisposeImage.argtypes = [vp]
    gp.GdipDisposeImage.restype = ci
    gp.GdipDeleteGraphics.argtypes = [vp]
    gp.GdipDeleteGraphics.restype = ci

    token = ctypes.c_size_t()
    status = gp.GdiplusStartup(ctypes.byref(token),
                               ctypes.byref(_StartupInput(1, None, 0, 0)), None)
    if status != 0:
        raise OSError("GdiplusStartup 失败，status=%d" % status)
    _gdiplus_token, _gp = token, gp
    return gp


def _normalize(buf, stride, height):
    """GDI+ 的行序可能自下而上，统一翻成自上而下。"""
    if stride > 0:
        return buf, stride
    s = -stride
    rows = [buf[i * s:(i + 1) * s] for i in range(height)]
    return b"".join(reversed(rows)), s


def _buf_luma(buf, stride, width, height):
    """抽样估算平均亮度（0-255）。"""
    total, n = 0, 0
    step_y = max(1, height // 24)
    step_x = max(1, width // 24)
    for y in range(0, height, step_y):
        base = y * stride
        for x in range(0, width, step_x):
            i = base + x * 4
            total += (buf[i + 2] * 299 + buf[i + 1] * 587 + buf[i] * 114) // 1000
            n += 1
    return total // n if n else 128


def _buf_to_ppm(buf, stride, width, height):
    """32bpp BGRA → PPM(P6) 字节串。"""
    out = bytearray()
    for y in range(height):
        start = y * stride
        row = bytearray(buf[start:start + width * 4])
        row[0::4], row[2::4] = row[2::4], row[0::4]   # BGRA → RGBA
        del row[3::4]                                 # 去掉 alpha，剩 RGB
        out += row
    return b"P6\n%d %d\n255\n" % (width, height) + bytes(out)


def _render(gp, src, out_w, out_h, src_rect, fill_argb=None):
    """把 src 的指定区域画到 out_w×out_h 的 32bpp 位图，可选叠一层半透明色。"""
    dst = ctypes.c_void_p()
    graphics = ctypes.c_void_p()
    try:
        if gp.GdipCreateBitmapFromScan0(out_w, out_h, 0, _PF_32BPP_ARGB,
                                        None, ctypes.byref(dst)) != 0:
            raise OSError("创建目标位图失败")
        if gp.GdipGetImageGraphicsContext(dst, ctypes.byref(graphics)) != 0:
            raise OSError("创建绘图上下文失败")
        gp.GdipSetInterpolationMode(graphics, 7)   # HighQualityBicubic
        gp.GdipSetPixelOffsetMode(graphics, 2)     # HighQuality
        gp.GdipSetSmoothingMode(graphics, 2)

        sx, sy, sw, sh = src_rect
        gp.GdipDrawImageRectRectI(graphics, src, 0, 0, out_w, out_h,
                                  sx, sy, sw, sh, _UNIT_PIXEL, None, None, None)

        # 半透明填充走 GDI+ 的 source-over 混合，比 Tk 的 stipple 网点干净得多
        if fill_argb is not None:
            brush = ctypes.c_void_p()
            if gp.GdipCreateSolidFill(fill_argb, ctypes.byref(brush)) == 0:
                gp.GdipFillRectangleI(graphics, brush, 0, 0, out_w, out_h)
                gp.GdipDeleteBrush(brush)

        rect = _GpRect(0, 0, out_w, out_h)
        data = _BitmapData()
        if gp.GdipBitmapLockBits(dst, ctypes.byref(rect), 1, _PF_32BPP_ARGB,
                                 ctypes.byref(data)) != 0:
            raise OSError("读取像素失败")
        try:
            buf = ctypes.string_at(data.Scan0, abs(data.Stride) * out_h)
            return _normalize(buf, data.Stride, out_h)
        finally:
            gp.GdipBitmapUnlockBits(dst, ctypes.byref(data))
    finally:
        if graphics:
            gp.GdipDeleteGraphics(graphics)
        if dst:
            gp.GdipDisposeImage(dst)


def _load_gdiplus(path, width, height, overlay=25):
    """GDI+ 解码、按 cover 缩放，并把遮罩直接合成进像素。"""
    gp = _gdiplus()
    src = ctypes.c_void_p()
    if gp.GdipCreateBitmapFromFile(path, ctypes.byref(src)) != 0 or not src:
        raise OSError("GDI+ 无法解码该文件")

    try:
        iw, ih = ctypes.c_uint(), ctypes.c_uint()
        gp.GdipGetImageWidth(src, ctypes.byref(iw))
        gp.GdipGetImageHeight(src, ctypes.byref(ih))
        iw, ih = int(iw.value), int(ih.value)
        if iw <= 0 or ih <= 0:
            raise OSError("图片尺寸无效")

        # cover：按最大缩放比取源图中心区域，避免拉伸变形
        scale = max(width / float(iw), height / float(ih))
        sw = min(iw, max(1, int(round(width / scale))))
        sh = min(ih, max(1, int(round(height / scale))))
        sx, sy = max(0, (iw - sw) // 2), max(0, (ih - sh) // 2)
        rect = (sx, sy, sw, sh)

        # 先用缩略图估亮度，决定遮罩该压白还是压黑
        tw, th = 40, max(1, int(round(40 * height / float(width))))
        small_buf, small_stride = _render(gp, src, tw, th, rect)
        luma = _buf_luma(small_buf, small_stride, tw, th)

        fill = None
        if overlay > 0:
            alpha = max(0, min(255, int(round(overlay * 255 / 100.0))))
            tone = 0x000000 if luma < 120 else 0xFFFFFF
            fill = (alpha << 24) | tone

        buf, stride = _render(gp, src, width, height, rect, fill)
        return _buf_to_ppm(buf, stride, width, height), luma
    finally:
        gp.GdipDisposeImage(src)


def _load_pillow(path, width, height, overlay=25):
    """Pillow 兜底（非 Windows 或装了 Pillow 时）。"""
    from PIL import Image        # noqa: F401 —— 可选依赖

    im = Image.open(path).convert("RGB")
    iw, ih = im.size
    scale = max(width / float(iw), height / float(ih))
    sw, sh = int(width / scale), int(height / scale)
    left, top = (iw - sw) // 2, (ih - sh) // 2
    im = im.crop((left, top, left + sw, top + sh)).resize((width, height), Image.LANCZOS)

    gray = im.convert("L").resize((32, 32))
    px = list(gray.getdata())
    luma = int(sum(px) / len(px))

    if overlay > 0:
        tone = (0, 0, 0) if luma < 120 else (255, 255, 255)
        im = Image.blend(im, Image.new("RGB", im.size, tone), overlay / 100.0)
    return im, luma


def load_photo(master, path, width, height, overlay=25):
    """把图片按 cover 方式加载成指定尺寸，返回 (PhotoImage, 平均亮度)。"""
    errors = []

    # 1) GDI+：Windows 上支持任意格式且缩放质量好
    if sys.platform == "win32":
        try:
            ppm, luma = _load_gdiplus(path, width, height, overlay)
            fd, tmp = tempfile.mkstemp(suffix=".ppm")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(ppm)
                photo = tk.PhotoImage(master=master, file=tmp)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return photo, luma
        except Exception as e:
            errors.append("GDI+: %s" % e)

    # 2) Pillow
    try:
        img, luma = _load_pillow(path, width, height, overlay)
        try:
            from PIL import ImageTk
            return ImageTk.PhotoImage(img, master=master), luma
        except Exception as e:
            errors.append("PIL.ImageTk: %s" % e)
    except Exception as e:
        errors.append("PIL: %s" % e)

    # 3) 原生 Tk：只认 PNG/GIF/PPM，且只能整数倍缩放后居中裁剪
    try:
        src = tk.PhotoImage(master=master, file=path)
        iw, ih = src.width(), src.height()
        # 整数倍缩小到刚好覆盖目标尺寸，再居中裁剪
        factor = 1
        while iw // (factor + 1) >= width and ih // (factor + 1) >= height:
            factor += 1
        if factor > 1:
            src = src.subsample(factor)
            iw, ih = src.width(), src.height()
        zoom = 1
        while iw * (zoom + 1) < width and ih * (zoom + 1) < height:
            zoom += 1
        if zoom > 1:
            src = src.zoom(zoom)
            iw, ih = src.width(), src.height()
        crop_w, crop_h = min(width, iw), min(height, ih)
        x, y = max(0, (iw - crop_w) // 2), max(0, (ih - crop_h) // 2)
        dst = tk.PhotoImage(master=master, width=width, height=height)
        dst.tk.call(dst, "copy", src, "-from", x, y, x + crop_w, y + crop_h,
                    "-to", (width - crop_w) // 2, (height - crop_h) // 2)
        return dst, 128
    except Exception as e:
        errors.append("Tk: %s" % e)

    raise RuntimeError("图片加载失败（%s）；建议另存为 PNG 后重试" % "；".join(errors))


# ---------------------------------------------------------------- 背景画布
class BackgroundCanvas(tk.Canvas):
    """内容区画布：负责画背景（渐变/图片）并承载页面块。"""

    def __init__(self, master, config=None):
        super().__init__(master, highlightthickness=0, bd=0, bg=COLORS["bg"])
        self._cfg = dict(DEFAULT_CONFIG)
        if config:
            self._cfg.update({k: v for k, v in config.items() if v is not None})
        self._photos = []
        self._avg_luma = None
        self._resize_job = None
        self._page = None
        self._pending = None
        self._page_seq = 0
        self._image_error = ""
        self._size_ready = False
        self.bind("<Configure>", self._on_configure)
        self.after(1, self.redraw)

    # ---------- 配置 ----------
    @property
    def config(self):
        return dict(self._cfg)

    @property
    def image_error(self):
        return self._image_error

    def apply_config(self, cfg):
        self._cfg.update(cfg)
        self._image_error = ""
        self.redraw()

    def is_dark(self):
        if self._cfg.get("type") == "image" and self._avg_luma is not None:
            return self._avg_luma < 120
        preset = PRESET_MAP.get(self._cfg.get("preset", "slate"), PRESET_MAP["slate"])
        return preset["dark"]

    def title_color(self):
        return "#FFFFFF" if self.is_dark() else COLORS["text"]

    def subtitle_color(self):
        return "#CBD5E1" if self.is_dark() else COLORS["muted"]

    # ---------- 绘制 ----------
    def _on_configure(self, _=None):
        if self._resize_job:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        # 首次拿到真实尺寸时立刻画，不必等节流，避免窗口起步闪一下空白
        if not self._size_ready:
            self._size_ready = True
            self.redraw()
            if self._page is not None:
                self._page.layout()
            return
        # 拖动窗口时高频重绘大图会卡，等尺寸稳定后再画
        self._resize_job = self.after(70, self._resize_settled)

    def _resize_settled(self):
        self._resize_job = None
        self.redraw()
        if self._page is not None:
            self._page.layout()

    def redraw(self):
        self.delete("bg")
        self._photos = []
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return

        if self._cfg.get("type") == "image" and self._cfg.get("image"):
            if not self._draw_image(width, height):
                self._draw_gradient(width, height)
        else:
            self._draw_gradient(width, height)

        # 背景永远在最底层，页面块浮在上面
        self.tag_lower("bg")

    def _draw_gradient(self, width, height):
        preset = PRESET_MAP.get(self._cfg.get("preset", "slate"), PRESET_MAP["slate"])
        self.configure(bg=preset["top"])
        steps = max(2, min(height, 200))
        for i in range(steps):
            t = i / float(steps - 1)
            color = lerp(preset["top"], preset["bottom"], t)
            y1 = height * i / steps
            y2 = height * (i + 1) / steps
            self.create_rectangle(0, y1, width, y2 + 1, fill=color,
                                  outline="", tags="bg")

    def _draw_image(self, width, height):
        path = self._cfg.get("image") or ""
        if not os.path.isfile(path):
            self._image_error = "图片文件不存在，已回退到预设配色"
            return False
        try:
            photo, luma = load_photo(self, path, width, height,
                                     int(self._cfg.get("overlay", 25)))
        except Exception as e:
            self._image_error = str(e)
            return False

        self._photos.append(photo)
        self._avg_luma = luma
        # 遮罩已经合成进像素，这里只负责贴图
        self.create_image(0, 0, image=photo, anchor="nw", tags="bg")
        self._image_error = ""
        return True

    # ---------- 页面 ----------
    def clear_page(self):
        for page in (self._page, self._pending):
            if page is not None:
                page.clear()
        self._page = None
        self._pending = None
        self.delete("page")

    def new_page(self, padx=30, pady=24):
        """开一页新布局器。

        旧页面先留着不删：新页面在隐藏状态下建好、排好版，最后由
        commit_page 一次性替换。这样构建过程中的中间重绘不会露出
        「只剩背景、卡片还没摆上来」的空白帧——也就是切换页面时那一下闪。
        """
        if self._pending is not None:
            # 上一页还没提交就又被切走（连点两个菜单），直接丢弃
            self._pending.clear()
            self._pending = None
        self._page_seq += 1
        self._pending = PageLayout(self, padx=padx, pady=pady,
                                   tag="pg%d" % self._page_seq)
        return self._pending

    def commit_page(self, page):
        """提交新页面：显示新元素、销毁旧页面，动作在同一次重绘里完成。"""
        if page is not self._pending:
            return
        self._pending = None
        old, self._page = self._page, page
        page.reveal()
        if old is not None:
            old.clear()
        self.tag_lower("bg")


# ---------------------------------------------------------------- 页面布局
class PageLayout:
    """在画布上自上而下摆放块：页头、统计行、整块卡片。"""

    def __init__(self, canvas, padx=30, pady=24, tag="page"):
        self.canvas = canvas
        self.tag = tag
        self.padx = padx
        self.pady = pady
        self.rows = []
        self._widgets = []
        self._items = []
        self._committed = False
        self._retry_job = None

    # ---------- 构建 ----------
    def clear(self):
        for w in self._widgets:
            try:
                w.destroy()
            except Exception:
                pass
        # 分隔条的事件绑在画布 tag 上，页面销毁时要一并解绑，
        # 否则回调闭包会一直抓着这一页不放
        for row in self.rows:
            if row.get("kind") == "sash":
                for seq in ("<Button-1>", "<Double-Button-1>", "<Enter>", "<Leave>"):
                    try:
                        self.canvas.tag_unbind(row["tag"], seq)
                    except Exception:
                        pass
        for seq in ("<B1-Motion>", "<ButtonRelease-1>"):
            try:
                self.canvas.unbind(seq)
            except Exception:
                pass
        self._widgets = []
        self.rows = []
        self._items = []
        try:
            self.canvas.delete(self.tag)
        except Exception:
            pass

    def _track(self, widget):
        self._widgets.append(widget)
        return widget

    def _spot(self, item):
        """新元素先藏起来，等整页排好版再统一显示，避免中途露白。"""
        self._items.append(item)
        try:
            self.canvas.itemconfigure(item, state="hidden")
        except tk.TclError:
            pass
        return item

    def reveal(self):
        for item in self._items:
            try:
                self.canvas.itemconfigure(item, state="normal")
            except tk.TclError:
                pass

    def header(self, title, subtitle="", actions=None, links=None, gap=0):
        """页头：标题与副标题直接画在背景上，右侧可挂文字链接或按钮。

        文字链接不做圆角底，正好避开 Canvas 无法透明的问题——在图片背景上
        也只有字形本身，不会出现一圈方形色块。
        """
        color = self.canvas.title_color()
        sub_color = self.canvas.subtitle_color()
        title_item = self.canvas.create_text(
            0, 0, text=title, anchor="nw", fill=color,
            font=font(21, True), tags=self.tag)
        # 字号在高 DPI 下实际行高不定，量一次 bbox 再决定副标题位置。
        # bbox 对隐藏元素返回 None，所以先量后藏。
        box = self.canvas.bbox(title_item)
        title_h = (box[3] - box[1]) if box else 30
        self._spot(title_item)
        items = [title_item]
        height = title_h
        sub_item, sub_y = None, 0
        if subtitle:
            sub_y = title_h + 6
            sub_item = self.canvas.create_text(
                0, 0, text=subtitle, anchor="nw", fill=sub_color,
                font=font(10), tags=self.tag)
            sbox = self.canvas.bbox(sub_item)
            items.append(sub_item)
            height = sub_y + ((sbox[3] - sbox[1]) if sbox else 16)
            self._spot(sub_item)

        link_color = "#93C5FD" if self.canvas.is_dark() else COLORS["primary"]
        link_items = []
        for text, command in (links or []):
            item = self.canvas.create_text(
                0, 0, text=text, anchor="ne", fill=link_color,
                font=font(10, True), tags=self.tag)
            # 宽度要在隐藏前量好，隐藏后 bbox 返回 None
            lbox = self.canvas.bbox(item)
            link_w = (lbox[2] - lbox[0]) if lbox else 64
            self.canvas.tag_bind(item, "<Button-1>",
                                 lambda _e, c=command: c())
            self.canvas.tag_bind(item, "<Enter>",
                                 lambda _e: self.canvas.configure(cursor="hand2"))
            self.canvas.tag_bind(item, "<Leave>",
                                 lambda _e: self.canvas.configure(cursor=""))
            link_items.append((item, link_w))
            self._spot(item)

        btns = []
        for w in (actions or []):
            self._track(w)
            item = self.canvas.create_window(0, 0, window=w, anchor="ne",
                                             tags=self.tag)
            btns.append((w, item))
            self._spot(item)

        self.rows.append({
            "kind": "header", "items": items, "sub": sub_item, "sub_y": sub_y,
            "links": link_items, "btns": btns, "height": height, "gap": gap,
        })

    def stat_row(self, cards, gap=14, gap_before=18):
        """等宽统计卡片行。"""
        for card in cards:
            card.update_idletasks()          # 先算好请求尺寸，否则高度会取到 1
        items = []
        for card in cards:
            self._track(card)
            item = self.canvas.create_window(0, 0, window=card, anchor="nw",
                                             tags=self.tag)
            items.append(item)
            self._spot(item)
        self.rows.append({"kind": "stat", "items": items, "cards": list(cards),
                          "height": max([c.winfo_reqheight() for c in cards] + [84]),
                          "gap": gap_before, "gapx": gap})

    def block(self, widget, gap=14, expand=False):
        """整宽卡片块；expand=True 时占满剩余高度。"""
        widget.update_idletasks()
        self._track(widget)
        item = self.canvas.create_window(0, 0, window=widget, anchor="nw",
                                         tags=self.tag)
        self._spot(item)
        self.rows.append({"kind": "block", "item": item, "widget": widget,
                          "expand": expand, "gap": gap,
                          "height": widget.winfo_reqheight()})

    # ---------- 可拖拽分隔条 ----------
    SASH_HEIGHT = 14        # 分隔条占的垂直空间
    MIN_TAIL = 120          # 与 layout 里 expand 块的最小高度一致，避免底部溢出

    def resizable_block(self, widget, gap=14, height=None, min_height=180,
                        on_change=None, on_release=None, tip=None):
        """注册一个高度可用鼠标拖动的卡片块，并在其下方画一条分隔线。

        height 是初始/期望高度；窗口不够高时只临时收缩，窗口变大后自动恢复
        到用户设定的高度。on_change(h) 在拖动中回调，on_release(h) 在松手时
        回调（适合落盘）。分隔条双击可恢复默认高度。

        分隔线直接画在画布上而不是用 Frame，这样在图片壁纸上也不会出现
        一条不透明的色带。
        """
        widget.update_idletasks()
        self._track(widget)
        item = self.canvas.create_window(0, 0, window=widget, anchor="nw",
                                         tags=self.tag)
        self._spot(item)
        want = int(height if height is not None else widget.winfo_reqheight())
        row = {
            "kind": "block", "item": item, "widget": widget, "expand": False,
            "gap": gap, "height": want, "resizable": True,
            "desired": want, "default": want, "min_height": int(min_height),
            "max_allowed": want, "on_change": on_change,
            "on_release": on_release,
        }
        self.rows.append(row)

        canvas = self.canvas
        sash_tag = "%s_sash%d" % (self.tag, len(self.rows))
        common = (self.tag, sash_tag)
        line_l = self._spot(canvas.create_line(
            0, 0, 0, 0, fill=COLORS["border"], width=2, tags=common))
        line_r = self._spot(canvas.create_line(
            0, 0, 0, 0, fill=COLORS["border"], width=2, tags=common))
        grip = self._spot(canvas.create_rectangle(
            0, 0, 0, 0, fill=COLORS["card"], outline=COLORS["border"],
            width=1, tags=common))
        label = canvas.create_text(
            0, 0, text=tip or "⇕ 拖动调整高度", fill=COLORS["muted"],
            font=font(9), tags=common)
        box = canvas.bbox(label)          # 隐藏后 bbox 会返回 None，先量宽度
        tip_w = (box[2] - box[0]) if box else 110
        self._spot(label)
        sash_row = {
            "kind": "sash", "gap": 0, "height": self.SASH_HEIGHT,
            "target": row, "tag": sash_tag,
            "items": (line_l, line_r, grip, label),
            "half": max(56, tip_w / 2.0 + 14),
        }
        self.rows.append(sash_row)

        state = {"y": None, "h": 0}

        def set_height(new_h, release=False):
            """拖动、双击复原、外部代码都走这里，保证行为一致。"""
            new_h = int(max(row["min_height"],
                            min(row["max_allowed"], new_h)))
            if new_h != row["desired"]:
                row["desired"] = new_h
                self.layout()
                if on_change:
                    try:
                        on_change(new_h)
                    except Exception:
                        pass
            if release and on_release:
                try:
                    on_release(new_h)
                except Exception:
                    pass
            return new_h

        def finish(_event):
            canvas.unbind("<B1-Motion>")
            canvas.unbind("<ButtonRelease-1>")
            state["y"] = None
            if on_release:
                try:
                    on_release(row["desired"])
                except Exception:
                    pass

        def move(event):
            if state["y"] is None:
                return
            set_height(state["h"] + event.y_root - state["y"])
            return "break"

        def start(event):
            state["y"] = event.y_root
            # 以当前「实际显示高度」为起点：窗口不够高时默认高度会被临时
            # 压缩，若从期望高度起算，第一次拖动会突然跳一下
            state["h"] = row["height"]
            # 拖动时鼠标会离开细细的分隔条，改为在整块画布上接 B1-Motion
            canvas.bind("<B1-Motion>", move)
            canvas.bind("<ButtonRelease-1>", finish)
            return "break"

        def reset(_event=None):
            set_height(row["default"], release=True)
            return "break"

        sash_row["set_height"] = set_height
        sash_row["reset"] = reset
        canvas.tag_bind(sash_tag, "<Button-1>", start)
        canvas.tag_bind(sash_tag, "<Double-Button-1>", reset)
        canvas.tag_bind(sash_tag, "<Enter>",
                        lambda _e: canvas.configure(cursor="sb_v_double_arrow"))
        canvas.tag_bind(sash_tag, "<Leave>",
                        lambda _e: canvas.configure(cursor=""))
        return row

    # ---------- 排布 ----------
    def layout(self):
        canvas = self.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width <= 1 or height <= 1:
            # 页面首次渲染时画布可能还没分配尺寸，催一次几何计算
            canvas.update_idletasks()
            width, height = canvas.winfo_width(), canvas.winfo_height()
        if width <= 1 or height <= 1:
            # 还是没尺寸：稍后再排。此间旧页面继续显示，不提交，避免露白
            if self._retry_job is None:
                self._retry_job = canvas.after(16, self._retry_layout)
            return
        if not self.rows:
            self._commit()
            return

        x0, right = self.padx, width - self.padx
        content_w = max(120, right - x0)
        y = self.pady

        for row in self.rows:
            y += row.get("gap", 0)
            kind = row["kind"]

            if kind == "header":
                canvas.coords(row["items"][0], x0, y)
                if row["sub"]:
                    canvas.coords(row["sub"], x0, y + row["sub_y"])
                x = right
                for item, w in row["links"]:
                    canvas.coords(item, x, y + 6)
                    x -= w + 18
                for btn, item in row["btns"]:
                    w = btn.winfo_reqwidth()
                    canvas.coords(item, x, y + 2)
                    x -= w + 8
                y += row["height"]

            elif kind == "stat":
                n = len(row["items"])
                gapx = row.get("gapx", 14)
                cell = (content_w - gapx * (n - 1)) / float(n)
                for i, item in enumerate(row["items"]):
                    canvas.coords(item, x0 + i * (cell + gapx), y)
                    canvas.itemconfigure(item, width=int(cell),
                                         height=int(row["height"]))
                y += row["height"]

            elif kind == "block":
                h = row["height"]
                if row.get("resizable"):
                    # 窗口不够高时先让学员区收缩，窗口重新变高会自动回到
                    # 用户拖出来的期望高度；同时始终给下半区留出 MIN_TAIL。
                    allowed = max(
                        row["min_height"],
                        height - y - self.SASH_HEIGHT - self.MIN_TAIL - self.pady)
                    row["max_allowed"] = int(allowed)
                    h = max(row["min_height"], min(row["desired"], allowed))
                    row["height"] = int(h)
                if row["expand"]:
                    h = max(120, height - y - self.pady)
                row["height"] = int(h)
                canvas.coords(row["item"], x0, y)
                canvas.itemconfigure(row["item"], width=int(content_w), height=int(h))
                y += h

            elif kind == "sash":
                cy = y + self.SASH_HEIGHT / 2.0
                cx = x0 + content_w / 2.0
                half = row["half"]
                line_l, line_r, grip, label = row["items"]
                canvas.coords(line_l, x0, cy, cx - half - 6, cy)
                canvas.coords(line_r, cx + half + 6, cy, right, cy)
                canvas.coords(grip, cx - half, y + 1,
                              cx + half, y + self.SASH_HEIGHT - 1)
                canvas.coords(label, cx, cy)
                canvas.tag_raise(row["tag"])
                y += self.SASH_HEIGHT

        # 坐标全部就位后才让新页面露出来，并送走旧页面——同一次重绘完成，
        # 用户看不到中间状态
        self._commit()

    def _commit(self):
        if self._committed:
            return
        self._committed = True
        self.canvas.commit_page(self)

    def _retry_layout(self):
        self._retry_job = None
        self.layout()

    # ---------- 工具 ----------
    def relayout_after_idle(self):
        try:
            self.canvas.after(1, self.layout)
        except Exception:
            pass
