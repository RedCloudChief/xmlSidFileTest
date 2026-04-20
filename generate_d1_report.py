"""
generate_d1_report.py
Generates 'Esito controlli file Domanda' PDF reports that match the official
Do.Ri. reference format from the Controlli folder, pixel-perfect.
"""
import xml.etree.ElementTree as ET
import math
import os
import sys
import json
from datetime import datetime
from fpdf import FPDF
from fpdf.enums import XPos, YPos

try:
    from shapely.geometry import Polygon
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

try:
    from pyproj import Transformer
    _gb_west  = Transformer.from_crs("EPSG:3003", "EPSG:4326", always_xy=True)
    _gb_east  = Transformer.from_crs("EPSG:3004", "EPSG:4326", always_xy=True)
    PYPROJ_AVAILABLE = True
except Exception:
    PYPROJ_AVAILABLE = False

import urllib.request
import tempfile
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon as MplPolygon


# Administrative competence configuration
# Default to Casal Velino (1199), the main reference in our data
REPORTING_ADMIN_CODE = "1199"
REPORTING_CADASTRAL_CODE = "B895"  # Casal Velino

# Administration mapping cache
ADMIN_MAP = {}

def load_admin_map():
    global ADMIN_MAP
    if ADMIN_MAP:
        return
    # Path relative to script
    base_dir = os.path.dirname(os.path.abspath(__file__))
    geojson_path = os.path.join(base_dir, 'data', 'sid_amministrazioni.geojson')
    if os.path.exists(geojson_path):
        try:
            with open(geojson_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for feature in data.get('features', []):
                    props = feature.get('properties', {})
                    code = props.get('amministra')
                    name = props.get('amministr0')
                    if code is not None and name:
                        ADMIN_MAP[str(code)] = name
        except Exception:
            pass # Silently fail, fall back to code only



# ─── Gauss-Boaga → WGS84 ─────────────────────────────────────────────────────

def gb_to_wgs84(n, e):
    """Convert Gauss-Boaga (N, E) to (lat, lon) WGS84."""
    if PYPROJ_AVAILABLE:
        try:
            transformer = _gb_east if e > 2000000 else _gb_west
            lon, lat = transformer.transform(e, n)
            return lat, lon
        except Exception:
            pass
    # Fallback: approximate formula for peninsula (sufficient for map centering)
    # Uses Roma40/Hayford ellipsoid parameters
    import math
    is_east = e > 2000000
    x0 = 2520000 if is_east else 1500000
    lon0_r = math.radians(15 if is_east else 9)
    a  = 6378388.0
    f  = 1 / 297.0
    b  = a * (1 - f)
    e2 = 1 - (b/a)**2
    k0 = 0.9996
    x  = e - x0
    y  = n
    M  = y / k0
    mu = M / (a * (1 - e2/4 - 3*e2**2/64))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = mu + (3*e1/2 - 27*e1**3/32)*math.sin(2*mu) + (21*e1**2/16)*math.sin(4*mu)
    N1   = a / math.sqrt(1 - e2*math.sin(phi1)**2)
    T1   = math.tan(phi1)**2
    C1   = (e2/(1-e2)) * math.cos(phi1)**2
    R1   = a*(1-e2) / (1 - e2*math.sin(phi1)**2)**1.5
    D    = x / (N1 * k0)
    lat  = phi1 - (N1*math.tan(phi1)/R1) * (
        D**2/2 - (5+3*T1+10*C1-4*C1**2-9*e2)*D**4/24
    )
    lon  = lon0_r + (D - (1+2*T1+C1)*D**3/6) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lon)


# ─── Static map snapshot ─────────────────────────────────────────────────────

GOOGLE_API_KEY = "AIzaSyDfCPsN9FMueurdBHsjT2FvRlLVas0VIgU"  # kept for future use

OBJ_COLORS = {
    'ZD': ('#1565C0', '#BBDEFB', 2.5),  # stroke, fill, lw
    'SP': ('#006064', '#B2EBF2', 2.5),
    'OR': ('#1B5E20', '#C8E6C9', 1.5),
    'CE': ('#E65100', '#FFE0B2', 1.5),
    'SE': ('#4A148C', '#E1BEE7', 1.5),
    'OE': ('#B71C1C', '#FFCDD2', 1.5),
}
DEFAULT_COLOR = ('#333333', '#EEEEEE', 1.0)


def get_poly_area(pts):
    """Calculate polygon area using the Shoelace formula."""
    x = pts[:, 0]
    y = pts[:, 1]
    return 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def generate_planimetry_image(objects, fuoriuscite=None, dpi=150, target_width_mm=170, target_height_mm=None):
    """Generate a planimetric vector PNG using matplotlib (no external tiles).
    Objects drawn in true Gauss-Boaga scale with equal-aspect ratio.
    Returns raw PNG bytes, or None on failure.
    """
    # Matplotlib already imported at top
    try:
        order = {'ZD': 0, 'SP': 1, 'OR': 2, 'SE': 3, 'OE': 4, 'CE': 5}

        sorted_objs = sorted(objects.items(), key=lambda kv: order.get(kv[1]['pref'], 9))

        # If height is not specified, default to square aspect ratio
        t_h = target_height_mm if target_height_mm else target_width_mm
        aspect = target_width_mm / t_h
        # Adapt figure size to the target paper aspect
        # Base size around 8 inches for better stability
        fig_w = 8
        fig_h = fig_w / aspect
        
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor='#f8f9fa')
        ax.set_facecolor('#eef2f7')
        ax.tick_params(colors='#555', labelsize=6)
        for spine in ax.spines.values():
            spine.set_edgecolor('#cccccc')

        all_xs, all_ys = [], []
        legend_handles = []
        seen_prefs = set()

        for key, obj in sorted_objs:
            pts = obj.get('points', [])
            if not pts:
                continue

            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            all_xs.extend(xs)
            all_ys.extend(ys)

            stroke, fill, lw = OBJ_COLORS.get(obj['pref'], DEFAULT_COLOR)

            if obj.get('is_closed') and len(pts) >= 3:
                arr = np.array(pts)
                poly = MplPolygon(arr, closed=True,
                                  facecolor=fill, alpha=0.45,
                                  edgecolor=stroke,
                                  linewidth=lw, zorder=3)
                ax.add_patch(poly)
            else:
                ax.plot(xs, ys, color=stroke, linewidth=lw, zorder=3)

            # Vertex dots
            ax.scatter(xs, ys, s=8, color=stroke, zorder=4,
                       edgecolors='white', linewidths=0.5)

            # Label at centroid
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            ax.annotate(key, xy=(cx, cy), fontsize=6, fontweight='bold',
                        color='white', ha='center', va='center', zorder=5,
                        bbox=dict(boxstyle='round,pad=0.25', facecolor=stroke,
                                  alpha=0.85, edgecolor='none'))

            if obj['pref'] not in seen_prefs:
                seen_prefs.add(obj['pref'])
                legend_handles.append(
                    mpatches.Patch(facecolor=fill, edgecolor=stroke,
                                   linewidth=lw, label=obj['pref'], alpha=0.75))

        # ── Draw Highlights for Extruded Areas (Fuoriuscite) ──
        if fuoriuscite:
            seen_ext = False
            for f in fuoriuscite:
                f_polys = f.get('fuori_polygons', [])
                if not f_polys:
                    continue
                seen_ext = True
                
                largest_p = None
                max_area = -1
                
                for fpoly in f_polys:
                    # fpoly is e.g. [{'n': n, 'e': e}, ...]
                    arr_ext = np.array([[p['e'], p['n']] for p in fpoly])
                    if len(arr_ext) < 3:
                        continue
                    
                    area = get_poly_area(arr_ext)
                    if area > max_area:
                        max_area = area
                        largest_p = arr_ext

                    poly_ext = MplPolygon(arr_ext, closed=True,
                                          facecolor='#FF0000', alpha=0.8,
                                          edgecolor='#B71C1C', linewidth=1.5, zorder=10)
                    ax.add_patch(poly_ext)
                
                # Add 'SUPERFICIE ECCEDENTE' Callout only for the most significant portion
                # threshold of 0.5 sqm to avoid tiny fragments cluttering the map
                if largest_p is not None and max_area > 0.5:
                    ex_c = largest_p[:, 0].mean()
                    ny_c = largest_p[:, 1].mean()
                    ax.annotate("SUPERFICIE ECCEDENTE", xy=(ex_c, ny_c),
                                xytext=(20, 20), textcoords="offset points",
                                fontsize=6, fontweight='black', color='#B71C1C',
                                ha='left', va='bottom', zorder=25,
                                arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=.15",
                                                color='#B71C1C', linewidth=0.8),
                                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', 
                                          alpha=0.95, edgecolor='#B71C1C', linewidth=0.6))

            
            if seen_ext:
                legend_handles.append(
                    mpatches.Patch(facecolor='#FF0000', edgecolor='#B71C1C',
                                   linewidth=1.5, label='Fuori Limite (RED 80%)', alpha=0.8))

        if not all_xs:
            plt.close(fig)
            return None, None

        # ── Geographic Scaling Logic ──
        xmin, xmax = min(all_xs), max(all_xs)
        ymin, ymax = min(all_ys), max(all_ys)
        cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
        dx, dy = xmax - xmin, ymax - ymin
        
        # We want the objects to fit in the specified viewport (W x H) in the PDF.
        # Minimal safety margin (7%) to maximize representation area as requested.
        margin = 1.07
        paper_w_m = target_width_mm / 1000.0
        paper_h_m = (target_height_mm / 1000.0) if target_height_mm else paper_w_m
        
        raw_s_w = (dx * margin) / paper_w_m
        raw_s_h = (dy * margin) / paper_h_m
        min_S = max(raw_s_w, raw_s_h)
        
        # --- Ultra-Granular Scaling Logic ---
        # Calculate a tight best-fit scale with minimal rounding overhead.
        if min_S < 1000:
            # Round up to the next 10 (e.g. 512 -> 520)
            chosen_S = int(np.ceil(min_S / 10.0) * 10)
        elif min_S < 5000:
            # Round up to the next 50 (e.g. 1120 -> 1150)
            chosen_S = int(np.ceil(min_S / 50.0) * 50)
        elif min_S < 10000:
            # Round up to the next 250 (e.g. 6300 -> 6500)
            chosen_S = int(np.ceil(min_S / 250.0) * 250)
        else:
            # Round up to the next 1000
            chosen_S = int(np.ceil(min_S / 1000.0) * 1000)
        
        # Minimum baseline
        if chosen_S < 50: chosen_S = 50
        
        # Set viewport limits based on the chosen scale
        view_w_m = chosen_S * paper_w_m
        view_h_m = chosen_S * paper_h_m
        
        ax.set_xlim(cx - view_w_m/2, cx + view_w_m/2)
        ax.set_ylim(cy - view_h_m/2, cy + view_h_m/2)
        ax.set_aspect('equal', adjustable='box')




        # Viewport metric extent for positioning scale bar
        vw, vh = chosen_S * paper_w_m, chosen_S * paper_h_m
        xpad = vw * 0.08
        ypad = vh * 0.08

        ax.set_xlabel('E — Gauss-Boaga Roma40 (m)', fontsize=6, color='#666')
        ax.set_ylabel('N — Gauss-Boaga Roma40 (m)', fontsize=6, color='#666')
        ax.grid(True, color='#ccddee', linewidth=0.5, linestyle='--', zorder=1)
        ax.set_title('Planimetria Rilievo', fontsize=10, fontweight='bold',
                     color='#1a1a3e', pad=10)


        if legend_handles:
            ax.legend(handles=legend_handles, fontsize=7,
                      facecolor='white', edgecolor='#aaaaaa',
                      framealpha=0.90, loc='lower right')

        # ── Scale Bar Logic ──
        # Gauss-Boaga is in meters. We compute a "nice" unit based on map width.
        map_w = (xmax - xmin)
        units = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
        # Choose a unit that is ~15-25% of the map width
        bar_len = 100
        for u in units:
            if u > map_w * 0.15:
                bar_len = u
                break
        
        # Position in bottom-left corner with some padding from the spines
        # Use viewport relative positioning
        sb_x = (cx - vw/2) + vw * 0.05
        sb_y = (cy - vh/2) + vh * 0.05
        
        # ── Draw Multi-Segment Scale Bar ──
        # Main Line
        ax.plot([sb_x, sb_x + bar_len], [sb_y, sb_y], color='black', linewidth=2, zorder=20)
        
        # Start Tick (0m)
        ax.plot([sb_x, sb_x], [sb_y - vh*0.01, sb_y + vh*0.01], color='black', linewidth=1.5, zorder=20)
        ax.text(sb_x - vw*0.005, sb_y + vh*0.015, "0", ha='right', va='bottom', fontsize=7, color='black', zorder=21)
        
        # 10m Tick (Mandatory as requested)
        if bar_len > 10:
            ax.plot([sb_x + 10, sb_x + 10], [sb_y - vh*0.01, sb_y + vh*0.01], color='black', linewidth=1.5, zorder=20)
            ax.text(sb_x + 10, sb_y + vh*0.025, "10 m", ha='center', va='bottom', fontsize=7, fontweight='black', color='#1a1a3e', zorder=21)
            # Thicker dark segment for the 10m reference
            ax.plot([sb_x, sb_x + 10], [sb_y, sb_y], color='#1a1a3e', linewidth=4, zorder=21)
        elif bar_len == 10:
             ax.text(sb_x + 10, sb_y + vh*0.025, "10 m", ha='center', va='bottom', fontsize=7, fontweight='black', color='#1a1a3e', zorder=21)
             ax.plot([sb_x, sb_x + 10], [sb_y, sb_y], color='#1a1a3e', linewidth=4, zorder=21)

        # End Tick
        ax.plot([sb_x + bar_len, sb_x + bar_len], [sb_y - vh*0.01, sb_y + vh*0.01], color='black', linewidth=1.5, zorder=20)
        if bar_len != 10:
            ax.text(sb_x + bar_len + vw*0.005, sb_y + vh*0.015, f"{bar_len} m", ha='left', va='bottom', fontsize=7, color='black', zorder=21)
        
        # Numeric Scale Label (e.g. 1:1000)
        ax.text(sb_x, sb_y - vh*0.025, f"SCALA 1:{chosen_S}",
                ha='left', va='top', fontsize=9, fontweight='bold',
                color='black', zorder=21,
                bbox=dict(boxstyle='square,pad=0.15', facecolor='white', alpha=0.8, edgecolor='#cccccc'))

        fig.tight_layout(pad=1.5)

        fd, tmp = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        fig.savefig(tmp, dpi=dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor())
        
        plt.close(fig)
        with open(tmp, 'rb') as f:
            data_bytes = f.read()
        try:
            os.remove(tmp)
        except Exception:
            pass
        return data_bytes, chosen_S

    except Exception as ex:
        print(f"[planimetry] generation failed: {ex}")
        return None, None







# ─── Color constants matching the reference PDFs ────────────────────────────
HDR_ORANGE   = (227, 207, 156)  # Khaki/Tan for ALL table headers (#E3CF9C)
HDR_TEXT     = (0, 0, 128)      # Dark blue bold text in headers
ROW_YELLOW   = (255, 255, 224)  # Light yellow for normal data rows
ROW_RED_BG   = (255, 200, 200)  # Light red background for error cells
ERR_RED      = (255, 0, 0)      # Bright red text for errors
TITLE_RED    = (139, 0, 0)      # Dark red for main title/file line
SECTION_BLUE = (0, 0, 128)      # Dark blue for section/chapter titles


class D1Reporter(FPDF):
    """PDF reporter exactly matching the official Do.Ri. reference format."""

    def __init__(self, filename=""):
        super().__init__()
        self.xml_filename = filename
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        if self.page_no() > 1:
            self.set_font('Helvetica', '', 8)
            self.set_text_color(100, 100, 100)
            self.cell(0, 8, self.xml_filename, new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            self.cell(0, 8, 'Report esito controlli file Domanda',
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            self.set_draw_color(180, 180, 180)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', '', 8)
        self.set_text_color(0, 0, 0)
        self.cell(0, 10, str(self.page_no()), align='C')

    # ── Title page ──────────────────────────────────────────────────────────
    def add_title_page(self, data):
        self.add_page()
        self.set_font('Times', 'B', 28)
        self.set_text_color(*TITLE_RED)
        self.set_y(90)
        self.cell(0, 15, 'Esito controlli file Domanda.', new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
        self.ln(10)
        self.set_font('Helvetica', '', 10)
        self.cell(0, 7, f'FILE: {data["filename"]}', new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
        self.ln(20)
        self.set_text_color(0, 0, 0)
        self.cell(0, 10, f'Data: {data["date"]}', new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')

    # ── TOC ─────────────────────────────────────────────────────────────────
    def draw_toc(self, sections):
        self.set_y(30)
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(0, 0, 0)
        self.cell(0, 10, 'Riepilogo', new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')


        self.ln(5)
        self.set_font('Helvetica', '', 10)
        self.set_text_color(0, 0, 255)
        for text, page in sections:
            # Dots between text and page number, like the reference
            avail = 170 - self.get_string_width(text) - self.get_string_width(str(page))
            dot_w = self.get_string_width('.')
            dots = '.' * max(5, int(avail / dot_w))
            self.cell(0, 7, f'{text}{dots}{page}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ── Chapter and section titles ───────────────────────────────────────────
    def chapter_title(self, title):
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(*SECTION_BLUE)
        self.cell(0, 10, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
        self.ln(2)

    def section_title(self, title):
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(*SECTION_BLUE)
        self.cell(0, 8, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')

    def body_text(self, text, size=9):
        self.set_font('Helvetica', '', size)
        self.set_text_color(0, 0, 0)
        self.multi_cell(0, 5, text)
        self.ln(2)

    # ── Generic orange table header row ────────────────────────────────────
    def draw_orange_header(self, headers, widths, row_h=8):
        self.set_font('Helvetica', 'B', 8)
        self.set_fill_color(*HDR_ORANGE)
        self.set_text_color(*HDR_TEXT)
        self.set_draw_color(0, 0, 0)
        for i, h in enumerate(headers):
            self.cell(widths[i], row_h, h, border=1, align='C', fill=True)
        self.ln()
        # Reset colors
        self.set_text_color(0, 0, 0)
        self.set_fill_color(*ROW_YELLOW)


# ─── Formatting helpers ──────────────────────────────────────────────────────

def _fmt(val):
    """Format float with comma thousands-style, 2 decimal places.
    Examples: 372.33 → '372,33', 1.0 → '1', 1797.57 → '1797,57'
    """
    if val is None:
        return "0"
    if val == 0:
        return "0"
    # 2 decimal places, then clean trailing zeros
    s = f"{val:.2f}"
    integer_part, dec_part = s.split('.')
    # Remove trailing zeros
    dec_part = dec_part.rstrip('0')
    if dec_part:
        return f"{integer_part},{dec_part}"
    return integer_part


# ─── Geometry helpers ────────────────────────────────────────────────────────

def calculate_area(points):
    n = len(points)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        p1 = points[i]
        p2 = points[(i + 1) % n]
        area += p1[0] * p2[1]
        area -= p2[0] * p1[1]
    return abs(area) / 2.0


def calculate_length(points):
    total = 0.0
    for i in range(len(points) - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        total += math.sqrt(dx * dx + dy * dy)
    return total


def is_closed(points):
    if not points or len(points) < 2:
        return False
    return (math.isclose(points[0][0], points[-1][0], abs_tol=1e-5) and
            math.isclose(points[0][1], points[-1][1], abs_tol=1e-5))


def get_bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def bboxes_intersect(b1, b2):
    return not (b1[2] < b2[0] or b1[0] > b2[2] or b1[3] < b2[1] or b1[1] > b2[3])


def pts_close(p1, p2, tol=0.01):
    return abs(p1[0] - p2[0]) < tol and abs(p1[1] - p2[1]) < tol


def segment_intersect(p1, p2, p3, p4):
    """Return interior or endpoint intersection point or None. 
    Exactly matches the official SID math which catches slightly misaligned shared vertices
    but ignores perfectly collinear overlapping segments (denom == 0).
    """
    x1, y1 = p1; x2, y2 = p2
    x3, y3 = p3; x4, y4 = p4
    denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
    if abs(denom) < 1e-12:
        return None # Collinear or parallel
    ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / denom
    ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / denom
    eps = -1e-9 # Include endpoints
    if eps <= ua <= 1 - eps and eps <= ub <= 1 - eps:
        return (x1 + ua * (x2 - x1), y1 + ua * (y2 - y1))
    return None


# ─── Validation logic ────────────────────────────────────────────────────────

def check_duplicates(objects):
    all_dupes = []
    for key, obj in objects.items():
        pts = obj['points']
        n = len(pts)
        if n < 2:
            continue
            
        dupes = []
        # Exhaustive comparison of all pairs (i, j) except (0, n-1)
        # to ensure Point 1 and Point N identity is allowed, but every other overlap is caught.
        for i in range(n):
            for j in range(i + 1, n):
                if i == 0 and j == n - 1:
                    continue # Exclude comparison between the first and the last point
                
                pi = pts[i]
                pj = pts[j]
                
                # Round to 4 decimal places for floating point comparison consistency
                ri = (round(pi[0], 4), round(pi[1], 4))
                rj = (round(pj[0], 4), round(pj[1], 4))
                
                if ri == rj:
                    # Report the pair. Indices are 1-based (i+1, j+1).
                    # We report the higher index point as a duplicate of the lower index point.
                    dupes.append((j + 1, i + 1, pj))
        
        if dupes:
            all_dupes.append((key, dupes))
            
    return all_dupes


def check_self_intersection(points):
    n = len(points)
    if n < 4:
        return False
    for i in range(n - 1):
        for j in range(i + 2, n - 1):
            if i == 0 and j == n - 2:
                continue
            if segment_intersect(points[i], points[i + 1], points[j], points[j + 1]) is not None:
                return True
    return False


def check_containment(objects):
    """
    Checks if objects are contained in ZD or SP bounds.
    Returns a list of dicts for objects that 'fuoriescono'.
    """
    results = []
    if not SHAPELY_AVAILABLE:
        return results
    
    from shapely.geometry import Polygon, Point, LineString
    from shapely.ops import unary_union
    
    # 1. Identify Containers (ZD, SP)
    containers = {}
    for k, obj in objects.items():
        if obj['pref'] in ['ZD', 'SP'] and obj.get('is_closed') and len(obj.get('points', [])) >= 3:
            try:
                poly = Polygon(obj['points'])
                if not poly.is_valid:
                    poly = poly.buffer(0)
                containers[k] = poly
            except Exception:
                pass
    
    if not containers:
        return results

    try:
        union_containers = unary_union(list(containers.values()))
        union_buffered = union_containers.buffer(1e-4) # tolerance
    except Exception:
        return results

    # 2. Iterate Targets
    for k, obj in objects.items():
        if obj['pref'] in ['ZD', 'SP', 'CE']:
            continue
            
        pts = obj.get('points', [])
        if not pts:
            continue
            
        pids = obj.get('point_ids', [str(x) for x in range(1, len(pts) + 1)])
        punti_fuori = []
        for p, pid in zip(pts, pids):
            if not Point(p).intersects(union_buffered):
                punti_fuori.append(pid)
                
        area_out = 0.0
        fuori_poly_list = []
        if len(pts) >= 3 and obj.get('is_closed'):
            try:
                poly = Polygon(pts)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                outside_geom = poly.difference(union_containers)
                area_out = outside_geom.area
                
                # Extract coordinates for UI
                if area_out >= 0.01:
                    def _extract(geom):
                        p_list = []
                        if geom.geom_type == 'Polygon' and not geom.is_empty:
                            ext = [{'n': p[1], 'e': p[0]} for p in geom.exterior.coords]
                            p_list.append(ext)
                        elif getattr(geom, 'geoms', None):
                            for g in geom.geoms:
                                if g.geom_type == 'Polygon' and not g.is_empty:
                                    ext = [{'n': p[1], 'e': p[0]} for p in g.exterior.coords]
                                    p_list.append(ext)
                        return p_list

                    fuori_poly_list = _extract(outside_geom)
            except Exception:
                pass
                
        if area_out >= 0.01 or punti_fuori:
            best_container = None
            max_area = -1
            if len(pts) >= 3 and obj.get('is_closed'):
                try:
                    poly = Polygon(pts)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    for c_key, c_poly in containers.items():
                        ia = poly.intersection(c_poly).area
                        if ia > max_area:
                            max_area = ia
                            best_container = c_key
                except Exception:
                    pass
            
            if not best_container:
                best_container = list(containers.keys())[0] if containers else "-"
            
            intersezioni_str = "-"
            if best_container in containers and len(pts) >= 2:
                try:
                    c_poly = containers[best_container]
                    target_line = LineString(pts)
                    container_line = c_poly.exterior
                    ix = target_line.intersection(container_line)
                    ix_coords = []
                    
                    def add_pt(p):
                        ix_coords.append(f"E: {p.x:.2f}, N: {p.y:.2f}")
                        
                    if ix.geom_type == 'Point':
                        add_pt(ix)
                    elif hasattr(ix, 'geoms'):
                        for geom in ix.geoms:
                            if geom.geom_type == 'Point':
                                add_pt(geom)
                            elif geom.geom_type == 'LineString':
                                if geom.coords:
                                    ix_coords.append(f"E: {geom.coords[0][0]:.2f}, N: {geom.coords[0][1]:.2f}")
                                    ix_coords.append(f"E: {geom.coords[-1][0]:.2f}, N: {geom.coords[-1][1]:.2f}")
                    elif ix.geom_type == 'LineString':
                        if ix.coords:
                            ix_coords.append(f"E: {ix.coords[0][0]:.2f}, N: {ix.coords[0][1]:.2f}")
                            ix_coords.append(f"E: {ix.coords[-1][0]:.2f}, N: {ix.coords[-1][1]:.2f}")
                            
                    intersezioni_json = []
                    if ix_coords:
                        seen = set()
                        unique_ix = []
                        for coord_str in ix_coords:
                            if coord_str not in seen:
                                seen.add(coord_str)
                                unique_ix.append(coord_str)
                                # parse coordinates for json
                                try:
                                    parts = coord_str.split(',')
                                    e_val = float(parts[0].split(':')[1].strip())
                                    n_val = float(parts[1].split(':')[1].strip())
                                    intersezioni_json.append({'e': e_val, 'n': n_val})
                                except Exception:
                                    pass
                        intersezioni_str = "\n".join(unique_ix)
                except Exception:
                    pass
                
            results.append({
                'oggetto': k,
                'container': best_container,
                'punti_fuori': ", ".join(punti_fuori) if punti_fuori else "-",
                'area_out': area_out,
                'intersezioni_str': intersezioni_str,
                'intersezioni_json': intersezioni_json if 'intersezioni_json' in locals() else [],
                'fuori_polygons': fuori_poly_list
            })
            
    return results


def point_in_polygon(point, polygon):
    x, y = point
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def calculate_coperta_scoperta(zd_key, zd_obj, objects):
    zd_pts = zd_obj['points']
    if not zd_pts or not zd_obj.get('is_closed'):
        return 0.0, 0.0
    total_area = zd_obj.get('calculated_area', 0.0)
    coperta = 0.0
    for key, obj in objects.items():
        if not key.startswith('OR ') and not key.startswith('SP '):
            continue
        or_pts = obj['points']
        if not or_pts:
            continue
        cx = sum(p[0] for p in or_pts) / len(or_pts)
        cy = sum(p[1] for p in or_pts) / len(or_pts)
        if point_in_polygon((cx, cy), zd_pts):
            coperta += obj.get('calculated_area', 0.0)
    scoperta = max(0.0, total_area - coperta)
    return scoperta, coperta


# ─── XML Parsing ─────────────────────────────────────────────────────────────

def parse_xml(xml_input, filename="uploaded.xml"):
    if isinstance(xml_input, str) and os.path.exists(xml_input):
        tree = ET.parse(xml_input)
        filename = os.path.basename(xml_input)
        root = tree.getroot()
    else:
        # If input is bytes, ET.fromstring handles encoding declarations automatically.
        # This is much safer than decoding to string first.
        try:
            if isinstance(xml_input, (bytes, bytearray)):
                root = ET.fromstring(xml_input)
            else:
                # If it's already a string, we handle the case where it might have a declaration
                content = xml_input
                if 'encoding="ISO-8859-1"' in content:
                    content = content.replace('encoding="ISO-8859-1"', 'encoding="utf-8"')
                root = ET.fromstring(content)
        except Exception as e:
            # Fallback for weirdly encoded files that ET might miss
            if isinstance(xml_input, (bytes, bytearray)):
                try:
                    content = xml_input.decode('iso-8859-1')
                    # Remove or fix the declaration if it's now a string
                    if 'encoding=' in content:
                        import re
                        content = re.sub(r'encoding=["\'][^"\']+["\']', 'encoding="utf-8"', content)
                    root = ET.fromstring(content)
                except Exception:
                    # Re-raise original error if fallback fails
                    raise e
            else:
                raise e


    d_tag = root.tag
    d_type = d_tag.replace('Domanda_', '')
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    errors = []

    # ── 1. Schema validation ──────────────────────────────────────────────
    if d_type == "D3" and root.find('Dati_Tecnico') is not None:
        errors.append((
            "Validazione File XML Domanda",
            'L\'elemento "Domanda_D3" ha un elemento figlio non valido "Dati_Tecnico". '
            'Elenco di possibili elementi previsti: "Documenti, Manutenzioni, Righe_Rilievo, '
            'Righe_Elaborato, Domicilio, Procuratori, Periodi_Stagionalita".',
            "Errore"
        ))

    # ── 1b. Filename vs Tax Code check ─────────────────────────────────────
    cf_el = root.find('.//Codice_Fiscale_Richiedente')
    if cf_el is not None and cf_el.text:
        cf_val = cf_el.text.strip()
        if cf_val.lower() not in filename.lower():
            errors.append((
                "Validazione Nome File",
                f"Il nome del file XML non contiene il Codice Fiscale del richiedente ({cf_val}).",
                "Errore"
            ))



    # ── 2. Admin competence ───────────────────────────────────────────────
    admin_comp = ""
    tipo_admin = ""
    if d_type in ("D1", "D2"):
        dg = root.find('Dati_Generali_Rilascio')
    else:
        dg = root.find('Dati_Generali_Variazione')

    if dg is not None:
        admin_el = dg.find('Amministrazione_Competente')
        tipo_el  = dg.find('Tipo_Amministrazione_Competente')
        if admin_el is not None and admin_el.text:
            admin_comp = admin_el.text.strip()
        if tipo_el is not None and tipo_el.text:
            tipo_admin = tipo_el.text.strip()

        # Lookup full name
        load_admin_map()
        admin_display = admin_comp
        if admin_comp in ADMIN_MAP:
            admin_display = ADMIN_MAP[admin_comp]

        # ── 2a. "Dati Generali" competence check ──
        # Historically forced to Error in reference files, now dynamic.
        if admin_comp != REPORTING_ADMIN_CODE:
            errors.append((
                "Dati Generali",
                f"La domanda non è di competenza dell'Amministrazione. "
                f"La competenza riportata nella domanda è {tipo_admin} - {admin_display} .",
                "Errore"
            ))

        # ── 2b. Existence check (D3/D2) ──
        if d_type in ("D3", "D2"):
            # Check if admin_comp exists in our database
            is_known = admin_comp in ADMIN_MAP
            if not is_known:
                # If unknown, add the two "Inesistente" rows found in official error samples
                errors.append((
                    "Dati Generali Variazione Amministrazione Competente",
                    f"Amministrazione Competente {admin_comp} - Inesistente",
                    "Errore"
                ))
                errors.append((
                    "Dati Generali Variazione Amministrazione Competente",
                    f"Amministrazione Competente {admin_comp} - Inesistente",
                    "Errore"
                ))

            # Concession existence warning
            num_conc  = dg.find('Numero_Concessione_Rif')
            anno_conc = dg.find('Anno_Concessione_Rif')
            if num_conc is not None and num_conc.text and anno_conc is not None and anno_conc.text:
                errors.append((
                    "Dati Generali Variazione Esistenza Concessione oggetto di variazione",
                    f"La concessione oggetto di variazione: {num_conc.text}/{anno_conc.text} "
                    f"non è presenta nella banca dati. Pertanto le informazioni relative ai "
                    f"Concessionari non saranno disponibili.",
                    "Warning"
                ))

    # ── 3. Parse objects ──────────────────────────────────────────────────
    objects = {}
    obj_requested = root.find('Oggetti_Richiesti')
    if obj_requested is not None:
        for obj in obj_requested.findall('Oggetto'):
            pref_el = obj.find('Prefisso')
            prog_el = obj.find('Progressivo')
            if pref_el is not None and prog_el is not None and pref_el.text and prog_el.text:
                pref = pref_el.text.strip()
                prog = prog_el.text.strip()
                key  = f"{pref} {prog}"
                objects[key] = {
                    'pref': pref, 'prog': prog,
                    'points': [], 'point_ids': [],
                    'coord_type': 'unknown',
                    'reported_surface': 0.0,
                    'catasto_codes': [],
                    'is_closed': False,
                    'calculated_area': 0.0,
                    'length': 0.0,
                    'self_intersects': False,
                }

    # ── 4. Survey rows ────────────────────────────────────────────────────
    rilievo = root.find('Righe_Rilievo')
    if rilievo is not None:
        for riga in rilievo.findall('Riga_Rilievo'):
            pref_el  = riga.find('Prefisso')
            ident_el = riga.find('Identificativo')
            if pref_el is None or ident_el is None or not pref_el.text or not ident_el.text:
                continue
            key = f"{pref_el.text.strip()} {ident_el.text.strip()}"
            pt_id_el = riga.find('Identificativo_Punto')
            pt_id   = pt_id_el.text.strip() if pt_id_el is not None and pt_id_el.text else '?'

            p = None
            coord_type = 'unknown'

            gb = riga.find('Gauss-Boaga')
            if gb is not None:
                n_el = gb.find('Coordinata_Nord')
                e_el = gb.find('Coordinata_Est')
                if n_el is not None and e_el is not None and n_el.text and e_el.text:
                    try:
                        n = float(n_el.text.replace(',', '.'))
                        e = float(e_el.text.replace(',', '.'))
                        p = (e, n)
                        coord_type = 'gauss_boaga'
                    except ValueError:
                        pass

            wgs = riga.find('Coordinate_WGS84')
            if wgs is not None and p is None:
                lat_el = wgs.find('Latitudine_Nord')
                lng_el = wgs.find('Longitudine_Est')
                if lat_el is not None and lng_el is not None and lat_el.text and lng_el.text:
                    try:
                        lat = float(lat_el.text.replace(',', '.'))
                        lng = float(lng_el.text.replace(',', '.'))
                        p = (lng, lat)
                        coord_type = 'wgs84'
                    except ValueError:
                        pass

            if p and key in objects:
                objects[key]['points'].append(p)
                objects[key]['point_ids'].append(pt_id)
                if objects[key]['coord_type'] == 'unknown':
                    objects[key]['coord_type'] = coord_type

    # ── 5. Elaborato rows ─────────────────────────────────────────────────
    elaborato = root.find('Righe_Elaborato')
    if elaborato is not None:
        for riga in elaborato.findall('Riga_Elaborato'):
            pref_el  = riga.find('Prefisso_Oggetto')
            ident_el = riga.find('Identificativo_Oggetto')
            if pref_el is None or ident_el is None or not pref_el.text or not ident_el.text:
                continue
            key = f"{pref_el.text.strip()} {ident_el.text.strip()}"

            surf_el = riga.find('Superficie')
            if surf_el is not None and surf_el.text and key in objects:
                try:
                    objects[key]['reported_surface'] = float(surf_el.text.replace(',', '.'))
                except ValueError:
                    pass

            for cat in riga.findall('Riferimento_Catastale'):
                cod_el = cat.find('Codice_Comune')
                if cod_el is not None and cod_el.text:
                    code = cod_el.text.strip()
                    if key in objects:
                        objects[key]['catasto_codes'].append(code)
                    
                    # ── 5a. Cadastral competence check ──
                    if code != REPORTING_CADASTRAL_CODE:
                        errors.append((
                            "Righe Elaborato",
                            f"Oggetto {key}: {code} Codice Comune non rientra nelle competenze "
                            f"dell'Amministrazione.",
                            "Errore"
                        ))

    # ── 6. Compute geometry ───────────────────────────────────────────────
    for key, obj in objects.items():
        if not obj['points']:
            continue
        obj['is_closed'] = is_closed(obj['points'])
        obj['length']    = calculate_length(obj['points'])
        is_metric = (obj['coord_type'] == 'gauss_boaga')
        if is_metric and len(obj['points']) >= 3:
            obj['calculated_area'] = calculate_area(obj['points'])
        if len(obj['points']) >= 4:
            obj['self_intersects'] = check_self_intersection(obj['points'])

    return {
        'filename': filename,
        'date':     now,
        'd_type':   d_type,
        'objects':  objects,
        'errors':   errors,
    }


# ─── PDF Generation ──────────────────────────────────────────────────────────

def _draw_prelim_row(pdf, widths, ctrl, desc, tipo):
    """Draw one row of the Controlli preliminari table exactly matching the reference."""
    lines_ctrl = max(1, len(ctrl) // 22 + 1)
    lines_desc = max(1, len(desc) // 60 + 1)
    h = max(10, max(lines_ctrl, lines_desc) * 5 + 4)

    cur_y = pdf.get_y()
    # Page break check
    if cur_y + h > 278:
        pdf.add_page()
        pdf.draw_orange_header(['Controllo', 'Descrizione Anomalia', 'Tipo Anomalia'], widths)
        cur_y = pdf.get_y()

    is_err = (tipo == "Errore")

    # ── Controllo cell (light yellow, navy blue text) ──
    pdf.set_fill_color(*ROW_YELLOW)
    pdf.set_text_color(*SECTION_BLUE)
    pdf.set_font('Helvetica', '', 7)
    ctrl_x = pdf.get_x()
    pdf.rect(ctrl_x, cur_y, widths[0], h, 'FD')
    pdf.set_xy(ctrl_x + 1, cur_y + 1)
    pdf.multi_cell(widths[0] - 2, 4.5, ctrl, align='C')
    pdf.set_xy(ctrl_x + widths[0], cur_y)

    # ── Descrizione Anomalia cell ──
    if is_err:
        pdf.set_fill_color(*ROW_RED_BG)
        pdf.set_text_color(*ERR_RED)
        pdf.set_font('Helvetica', 'B', 7)
    else:
        pdf.set_fill_color(*ROW_YELLOW)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Helvetica', '', 7)

    desc_x = pdf.get_x()
    pdf.rect(desc_x, cur_y, widths[1], h, 'FD')
    pdf.set_xy(desc_x + 2, cur_y + 1)
    pdf.multi_cell(widths[1] - 4, 4.5, desc, align='C')
    pdf.set_xy(desc_x + widths[1], cur_y)

    # ── Tipo Anomalia cell ──
    if is_err:
        pdf.set_fill_color(255, 80, 80)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font('Helvetica', 'B', 8)
    else:
        pdf.set_fill_color(255, 200, 100)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Helvetica', 'B', 8)

    pdf.cell(widths[2], h, tipo, border=1, fill=True, align='C')
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_fill_color(*ROW_YELLOW)


def _draw_area_row(pdf, rw, key, obj, is_d3, objects):
    """Draw one row of the Chiusura/Area table matching the reference."""
    is_ce = (obj['pref'] == 'CE')
    is_zd = (obj['pref'] == 'ZD')
    is_sp = (obj['pref'] == 'SP')
    ca    = obj.get('calculated_area', 0.0)
    ra    = obj.get('reported_surface', 0.0)
    obj_closed = obj.get('is_closed', False)
    has_pts    = len(obj.get('points', [])) > 0

    # ── Area Chiusa ──
    if is_ce or not has_pts:
        area_closed = "-"
    elif obj_closed:
        area_closed = "Sì"
    else:
        area_closed = "No"

    # ── Auto Intersect ──
    if is_ce or not has_pts or not obj_closed:
        auto_int = "-"
    else:
        auto_int = "Sì" if obj.get('self_intersects') else "No"

    # ── Area value ── (always show calculated, even for CE)
    area_str = _fmt(ca)

    # ── Notes ──
    note_parts = []
    sup_val = _fmt(ra)
    if is_ce:
        note_parts.append(f"Superficie riportata nel documento è:  {sup_val}  m²")
        note_parts.append(f"Lunghezza dell'opera: {_fmt(obj['length'])} m")
    elif (is_d3 and (is_zd or is_sp)):
        note_parts.append(f"Superficie riportata nel documento è:  {sup_val}  m².")
        scoperta, coperta = calculate_coperta_scoperta(key, obj, objects)
        note_parts.append(f"La superficie scoperta calcolata è:  {_fmt(scoperta)}  m².")
        note_parts.append(f"La superficie coperta calcolata è:  {_fmt(coperta)}  m².")
    else:
        note_parts.append(f"Superficie riportata nel documento è:  {sup_val}  m².")

    note = "\n".join(note_parts)
    note_lines = note.count('\n') + 1

    # ── Warning? ──
    has_warning = False
    diff_area = 0.0
    if not is_ce:
        diff_area = abs(ca - ra)
        if diff_area >= 1.0:
            has_warning = True

    if is_ce:
        anomalia = "-"
    elif not has_pts:
        anomalia = "-"
    else:
        diff_str = f"{_fmt(diff_area)} m²"
        anomalia = f"Warning: {diff_str}" if has_warning else f"OK: {diff_str}"
    rh = max(12, note_lines * 6 + 4)

    cur_y = pdf.get_y()
    if cur_y + rh > 278:
        pdf.add_page()
        pdf.draw_orange_header(
            ['Oggetto', 'Chiusura', 'Autointersezione', 'Area calcolata', 'Note', 'Tipo di anomalia'],
            rw, row_h=10
        )
        pdf.set_font('Helvetica', '', 7)
        cur_y = pdf.get_y()

    pdf.set_fill_color(*ROW_YELLOW)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Helvetica', '', 7)
    pdf.set_draw_color(0, 0, 0)

    # Oggetto
    pdf.cell(rw[0], rh, key, border=1, fill=True, align='C')
    # Area Chiusa
    pdf.cell(rw[1], rh, area_closed, border=1, fill=True, align='C')
    # Auto Intersect
    pdf.cell(rw[2], rh, auto_int, border=1, fill=True, align='C')

    # Area (red if warning)
    area_x = pdf.get_x()
    if has_warning:
        pdf.set_text_color(*ERR_RED)
        pdf.set_font('Helvetica', 'B', 7)
    pdf.cell(rw[3], rh, area_str, border=1, fill=True, align='R')
    if has_warning:
        pdf.set_draw_color(*ERR_RED)
        pdf.rect(area_x + 0.5, cur_y + 0.5, rw[3] - 1, rh - 1)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Helvetica', '', 7)

    # Note (multi-line)
    note_x = pdf.get_x()
    pdf.rect(note_x, cur_y, rw[4], rh, 'FD')
    pdf.set_xy(note_x + 1, cur_y + 3.5)
    pdf.multi_cell(rw[4] - 2, 4.2, note, border=0, align='L')
    pdf.set_xy(note_x + rw[4], cur_y)


    # Tipo Anomalia
    if anomalia.startswith("Warning"):
        pdf.set_text_color(*ERR_RED)
        pdf.set_font('Helvetica', 'B', 7)
    elif anomalia.startswith("OK"):
        pdf.set_text_color(0, 150, 0)
        pdf.set_font('Helvetica', 'B', 7)
    else:
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Helvetica', '', 7)

    pdf.cell(rw[5], rh, anomalia, border=1, fill=True, align='C')
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Helvetica', '', 7)
    pdf.ln()


def _draw_containment_table(pdf, fuoriuscite):
    col_w = [13, 29, 25, 30, 20, 53]  # total = 170
    
    pdf.draw_orange_header(
        ['Oggetto', 'Zona di Riferimento', 'Numero Punti', 'Punti', 'Superficie', 'Coordinate Intersezione'],
        col_w, row_h=10
    )
    
    for f in fuoriuscite:
        area_text = f"{_fmt(f['area_out'])} m²"
        p_text = f['punti_fuori']
        ix_text = f.get('intersezioni_str', '-')
        if not ix_text:
            ix_text = '-'
            
        num_p_text = str(len(p_text.split(','))) if p_text != "-" else "0"
            
        lines_ix = max(1, ix_text.count('\n') + 1)
        rh = max(8, lines_ix * 5 + 4)
        
        cur_y = pdf.get_y()
        if cur_y + rh > 278:
            pdf.add_page()
            pdf.draw_orange_header(
                ['Oggetto', 'Zona di Riferimento', 'Numero Punti', 'Punti', 'Superficie', 'Coordinate Intersezione'],
                col_w, row_h=10
            )
            cur_y = pdf.get_y()
            
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(0, 0, 0)
        pdf.set_fill_color(*ROW_YELLOW)
        
        pdf.cell(col_w[0], rh, f['oggetto'], border=1, fill=True, align='C')
        pdf.cell(col_w[1], rh, f['container'], border=1, fill=True, align='C')
        pdf.cell(col_w[2], rh, num_p_text, border=1, fill=True, align='C')
        
        if len(p_text) > 30:
            p_text = p_text[:27] + "..."
            
        pdf.cell(col_w[3], rh, p_text, border=1, fill=True, align='C')
        
        if f['area_out'] > 0:
            pdf.set_text_color(*ERR_RED)
            pdf.set_font('Helvetica', 'B', 8)
            
        pdf.cell(col_w[4], rh, area_text, border=1, fill=True, align='C')
        
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Helvetica', '', 7)
        
        ix_x = pdf.get_x()
        pdf.rect(ix_x, cur_y, col_w[5], rh, 'FD')
        
        # Center horizontally and vertically a multi-line text
        text_height = lines_ix * 5
        start_y = cur_y + (rh - text_height) / 2
        pdf.set_xy(ix_x + 1, start_y)
        pdf.multi_cell(col_w[5] - 2, 5, ix_text, align='C')
        
        pdf.set_xy(10, cur_y + rh)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Helvetica', '', 9)



def generate_pdf(data, output_path):
    d_type = data['d_type']
    is_d3 = (d_type == "D3")
    xml_filename = data['filename']
    
    pdf = D1Reporter(xml_filename)
    pdf.set_title("Esito controlli file Domanda")
    
    # ── Title page ──
    pdf.add_title_page(data)
    
    # ── Geometry checks ──
    dupes = check_duplicates(data['objects'])
    fuoriuscite = check_containment(data['objects'])

    sections = []

    # ── 1. Placeholder for Riepilogo (TOC) ──
    # We add the page now, but will fill it at the end once we have all page numbers.
    pdf.add_page()
    toc_page_num = pdf.page_no()

    # ── 1. Controlli Rilievo ─────────────────────────────────────────────────
    pdf.add_page()
    page_rilievo = pdf.page_no()
    sections.append(("Controlli Rilievo", page_rilievo))
    pdf.chapter_title('Controlli Rilievo')

    pdf.body_text("Si riporta di seguito l'esito dei controlli eseguiti sul Rilievo Planimetrico.")

    # ── Duplicazione Punti ──────────────────────────────────────────────────
    pdf.section_title('Duplicazione Punti')
    pdf.ln(3)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(0, 0, 0)
    if not dupes:
        pdf.cell(0, 5, "Non sono presenti punti duplicati.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        # Table of duplicates per object
        w_dupes = [40, 40, 110]
        for key, dlist in dupes:
            pdf.set_font('Helvetica', 'B', 10)
            pdf.set_text_color(*SECTION_BLUE)
            pdf.cell(0, 8, f"Oggetto {key}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            pdf.draw_orange_header(['1° Punto', '2° Punto', 'Tipo Anomalia'], w_dupes)
            
            pdf.set_font('Helvetica', '', 9)
            pdf.set_fill_color(*ROW_YELLOW)
            pdf.set_text_color(0, 0, 0)
            for pt_idx, orig_idx, coord in dlist:
                # 1st point (the earlier occurrence)
                pdf.cell(w_dupes[0], 8, str(orig_idx), border=1, fill=True, align='C')
                # 2nd point (the duplicate)
                pdf.cell(w_dupes[1], 8, str(pt_idx), border=1, fill=True, align='C')
                # Anomaly description
                pdf.set_text_color(*ERR_RED)
                pdf.set_font('Helvetica', 'B', 8)
                pdf.cell(w_dupes[2], 8, "Warning - Sono punti duplicati", border=1, fill=True, align='C')
                pdf.set_text_color(0, 0, 0)
                pdf.set_font('Helvetica', '', 9)
                pdf.ln()
            pdf.ln(5)
    pdf.ln(5)

    # ── Chiusura, Auto-intersect e Area Oggetti ─────────────────────────────
    pdf.section_title('Chiusura, Auto-intersect e Area Oggetti')
    pdf.ln(3)

    rw = [22, 25, 30, 25, 62, 26]
    pdf.draw_orange_header(
        ['Oggetto', 'Chiusura', 'Autointersezione', 'Area calcolata', 'Note', 'Tipo di anomalia'],
        rw, row_h=10
    )
    
    # Sort objects by type and prog
    def sort_key(k_o):
        k, o = k_o
        p = o['pref']
        order = {'ZD': 1, 'SP': 2, 'OR': 3, 'CE': 4, 'SE': 5, 'OE': 6}
        return (order.get(p, 99), int(o['prog']) if o['prog'].isdigit() else o['prog'])

    sorted_objects = sorted(data['objects'].items(), key=sort_key)
    for key, obj in sorted_objects:
        _draw_area_row(pdf, rw, key, obj, is_d3, data['objects'])

    pdf.ln(5)

    # ── Intersezione tra Oggetti ────────────────────────────────────────────
    pdf.section_title('Intersezione tra Oggetti')
    pdf.ln(3)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(0, 0, 0)

    if not fuoriuscite:
        pdf.set_text_color(0, 150, 0)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(0, 5, "Non sono presenti intersezioni tra oggetti.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Helvetica', '', 9)
    else:
        pdf.body_text(
            "Si riportano di seguito gli oggetti che fuoriescono dalle Zone Demaniali (ZD) "
            "o Specchi Acquei (SP) di riferimento. Questa tipologia di anomalie è considerata "
            "un Warning e pertanto non impedisce l'import della Domanda."
        )
        pdf.ln(2)
        _draw_containment_table(pdf, fuoriuscite)

    pdf.ln(5)

    # ── 4. Planimetria Rilievo ─────────────────────────────────────────────────
    print("[planimetria] Generazione immagine vettoriale...")
    # Use A4 Portrait dimensions (approx 170x210mm) to minimize white space
    # while fitting the standard vertical format requested by the user.
    img_bytes, chosen_scale = generate_planimetry_image(data['objects'], fuoriuscite=fuoriuscite, target_width_mm=170, target_height_mm=210)
    if img_bytes:
        print(f"[planimetry] Image generated, size: {len(img_bytes)} bytes")


        pdf.add_page(format='A4', orientation='PORTRAIT')
        page_map = pdf.page_no()
        sections.append(("Planimetria Rilievo", page_map))
        pdf.chapter_title('Planimetria Rilievo')
        pdf.ln(2)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 5,
                 f'Planimetria vettoriale generata automaticamente dalle coordinate del rilievo. '
                 f'Scala di rappresentazione 1:{chosen_scale}',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

        # Centre on A4 Portrait (210x297)
        img_w = 170
        x_img = (210 - img_w) / 2
        import io
        print(f"[pdf] DEBUG: get_y() prima della mappa: {pdf.get_y()}")
        # Wrapping bytes in io.BytesIO ensures maximum compatibility with FPDF2 image detection
        pdf.image(io.BytesIO(img_bytes), x=x_img, y=pdf.get_y(), w=img_w, h=0)



    else:
        print("[map snapshot] Skipped (fetch failed or no objects).")

    # ── Finalize Riepilogo (TOC) ──
    # We go back to page 2 and draw the TOC there.
    last_page = pdf.page_no()
    pdf.page = toc_page_num
    # The sections list already has Preliminaries, Rilievo, and Map.
    # We insert Riepilogo at the start with its own page number.
    sections.insert(0, ("Riepilogo", toc_page_num))
    pdf.draw_toc(sections)
    
    # Reset to last page for clean output state
    pdf.page = last_page

    print(f"[pdf] Total pages generated: {last_page}")
    pdf.output(output_path)



def run_validation(xml_input, output_path, filename="uploaded.xml"):
    data = parse_xml(xml_input, filename)
    generate_pdf(data, output_path)
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: generate_d1_report.py <file.xml>")
        sys.exit(1)
    inp = sys.argv[1]
    out = inp.replace('.xml', '.ControlloFileDomanda.pdf')
    run_validation(inp, out)
    print(f"Generated: {out}")
