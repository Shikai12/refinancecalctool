#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
 build.py --  V3.13 (Strict Read-Only: scan, compare, append only. NEVER generate dates.)
 (V3.13):
  - Strict read-only: scan blog/*.html vs articles.json, append missing only
  - Date: ONLY read from HTML, NEVER generate. Missing date -> SKIP article
  - Existing articles: ALL fields protected, NEVER updated by build.py
  - One date per article: date_published = date = HTML date. date_modified = HTML date or same as published
  - Removed: ALL date generation functions (generate_random_past_date, set_site_date_range)
  - build.py task: scan -> compare -> append new -> update homepages -> update SEO files -> update archives
"""

import sys
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import json
import re
import hashlib
import copy
import time
try:
    import fcntl
except ImportError:
    class _FakeFcntl:
        LOCK_EX = 1
        LOCK_UN = 2
        @staticmethod
        def flock(*args, **kwargs):
            pass
    fcntl = _FakeFcntl()
from datetime import datetime
from pathlib import Path
from html.parser import HTMLParser
from collections import Counter
import html


# ───────────────────────────────────────────────
# ： +  + 
# ───────────────────────────────────────────────

class FileLock:
    """Cross-platform file lock (Linux/macOS uses fcntl, Windows uses msvcrt.locking)"""
    def __init__(self, lock_path, timeout=30):
        self.lock_path = Path(lock_path)
        self.lock_file = None
        self._has_lock = False
        self._timeout = timeout

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            self._lock_windows()
        else:
            self._lock_unix()
        return self

    def _lock_windows(self):
        import msvcrt
        for _ in range(self._timeout * 10):
            try:
                self.lock_file = open(self.lock_path, 'w')
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_LOCK, 1)
                self._has_lock = True
                return
            except (OSError, IOError):
                if self.lock_file:
                    self.lock_file.close()
                    self.lock_file = None
                time.sleep(0.1)
        raise TimeoutError(f"Cannot acquire file lock: {self.lock_path}")

    def _lock_unix(self):
        self.lock_file = open(self.lock_path, 'w')
        fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX)
        self._has_lock = True

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_file:
            if sys.platform == 'win32' and self._has_lock:
                try:
                    import msvcrt
                    msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except:
                    pass
            else:
                try:
                    fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                except:
                    pass
            self.lock_file.close()
        return False


def sha256_file(path):
    """Calculate file SHA256"""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(path):
    """Verify checksum before reading"""
    path = Path(path)
    checksum_path = Path(str(path) + '.checksum')
    if not checksum_path.exists() or not path.exists():
        return True
    try:
        expected = checksum_path.read_text(encoding='utf-8').strip()
        actual = sha256_file(path)
        if expected != actual:
            print(f"   WARN: checksum mismatch for {path.name}!")
            return False
        return True
    except Exception as e:
        print(f"   WARN: checksum verify failed for {path.name}: {e}")
        return True


def write_checksum(path):
    """Generate/update .checksum for main file"""
    checksum_path = Path(str(path) + '.checksum')
    if path.exists():
        try:
            checksum = sha256_file(path)
            checksum_path.write_text(checksum, encoding='utf-8')
        except OSError as e:
            print(f"   WARN: write_checksum failed for {path.name}: {e}")


# ═══════════════════════════════════════════════════
# 全局备份配置
# ═══════════════════════════════════════════════════
BUILD_BACKUP_DIR = Path('.build_backups')
BUILD_MAX_BACKUPS = 10  # 每个文件保留最近N个备份


def backup_file(path, max_backups=BUILD_MAX_BACKUPS, use_global_dir=True):
    """
    自动备份文件
    - 原文件旁生成 .bak.时间戳
    - 统一备份目录 .build_backups/ 也存一份（如果 use_global_dir=True）
    """
    path = Path(path)
    if not path.exists():
        return None

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 1. 原文件旁备份
    local_bak = path.parent / f"{path.name}.bak.{timestamp}"
    local_bak.write_bytes(path.read_bytes())

    # 2. 统一备份目录
    global_bak = None
    if use_global_dir:
        BUILD_BACKUP_DIR.mkdir(exist_ok=True)
        # 按文件类型分子目录
        sub_dir = BUILD_BACKUP_DIR / path.name
        sub_dir.mkdir(exist_ok=True)
        global_bak = sub_dir / f"{timestamp}.bak"
        global_bak.write_bytes(path.read_bytes())

    # 3. 清理旧备份（本地）
    all_local_baks = sorted(
        path.parent.glob(f"{path.name}.bak.*"),
        key=lambda p: p.stat().st_mtime
    )
    for old_bak in all_local_baks[:-max_backups]:
        old_bak.unlink()

    # 4. 清理旧备份（全局目录）
    if use_global_dir and global_bak:
        sub_dir = BUILD_BACKUP_DIR / path.name
        all_global_baks = sorted(sub_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime)
        for old_bak in all_global_baks[:-max_backups]:
            old_bak.unlink()

    return {
        'local': str(local_bak),
        'global': str(global_bak) if global_bak else None,
        'timestamp': timestamp
    }


def get_backup_report():
    """获取本次 build 的备份报告"""
    if not BUILD_BACKUP_DIR.exists():
        return "无备份"

    lines = ["\n" + "=" * 60, "📦 备份报告", "=" * 60]
    total_files = 0
    total_backups = 0

    for sub_dir in sorted(BUILD_BACKUP_DIR.iterdir()):
        if sub_dir.is_dir():
            backups = list(sub_dir.glob("*.bak"))
            total_files += 1
            total_backups += len(backups)
            lines.append(f"  {sub_dir.name}: {len(backups)} 个备份")

    lines.append(f"\n  总计: {total_files} 个文件, {total_backups} 个备份")
    lines.append("=" * 60)
    return "\n".join(lines)


def atomic_write_json(path, data, indent=2, ensure_ascii=False):
    """Atomic write JSON"""
    path = Path(path)
    tmp_path = path.parent / f"{path.name}.tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
        if path.exists():
            backup_file(path)
        tmp_path.replace(path)
        write_checksum(path)
        return True
    except Exception as e:
        print(f"   ERROR: atomic_write_json failed: {e}")
        raise
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def atomic_write_text(path, text, encoding='utf-8'):
    """Atomic write text file"""
    path = Path(path)
    tmp_path = path.parent / f"{path.name}.tmp"
    try:
        with open(tmp_path, 'w', encoding=encoding) as f:
            f.write(text)
        if path.exists():
            backup_file(path)
        tmp_path.replace(path)
        return True
    except Exception as e:
        print(f"   ERROR: atomic_write_text failed: {e}")
        raise
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════
# GlobalGuard 量子防护系统（内嵌，无需外部依赖）
# ═══════════════════════════════════════════════════
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Global Archive 量子防护脚本
===========================
核心原则：只允许追加，禁止修改已有内容

功能：
1. 安全更新 global_archive.json —— 只新增，不修改已有数据
2. 数据完整性校验 —— 写入前验证必填字段
3. 自动备份 —— 每次写入前自动备份原文件
4. 字段锁定 —— 已有文章的 slug/title/date 等关键字段不可被覆盖
5. 增量合并 —— 新站点/新文章/新工具 安全追加

使用方法：
    # GlobalGuard 已内嵌，直接使用:
    # guard = GlobalGuard("path/to/global_archive.json")
    guard.merge_new_articles("site_key", [new_article1, new_article2])
    guard.save()
"""

import json
import os
import shutil
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional


class GlobalGuard:
    """Global Archive 量子防护管理器"""

    # 关键保护字段 —— 已有数据中的这些字段不可被覆盖
    PROTECTED_ARTICLE_FIELDS = [
        'slug', 'title', 'date_published', 'date_modified', 
        'date', 'date_iso', 'author', 'word_count'
    ]

    # 必填字段校验
    REQUIRED_SITE_FIELDS = [
        'site_name', 'domain', 'prefix', 'author_info', 'persona',
        'articles', 'tools', 'stats', 'updated_at', 'keyword_map',
        'config', 'location', 'local_events'
    ]

    REQUIRED_AUTHOR_FIELDS = ['name', 'job_title', 'location']
    REQUIRED_PERSONA_FIELDS = ['name', 'age', 'location', 'occupation', 'education', 'writing_style', 'expertise', 'bio']
    REQUIRED_STATS_FIELDS = ['total_articles', 'total_tools', 'total_words']

    def __init__(self, filepath: str):
        self.filepath = os.path.abspath(filepath)
        self.data = self._load()
        self.original_hash = self._compute_hash(self.data)
        self.changes_log = []

    def _load(self) -> dict:
        """加载 global_archive.json"""
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"Global archive not found: {self.filepath}")
        with open(self.filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _compute_hash(self, data: dict) -> str:
        """计算数据哈希，用于检测是否被篡改"""
        return hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')
        ).hexdigest()[:16]

    def _backup(self):
        """自动备份原文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.filepath + f".bak.{timestamp}"
        shutil.copy2(self.filepath, backup_path)
        self.changes_log.append(f"[BACKUP] 已备份到: {backup_path}")
        return backup_path

    def validate(self, raise_on_error: bool = True) -> List[str]:
        """
        校验数据完整性
        返回错误列表，如果 raise_on_error=True 则抛出异常
        """
        errors = []
        sites = self.data.get('sites', {})

        if not sites:
            errors.append("sites 字典为空")

        for site_key, site_data in sites.items():
            # 检查必填字段
            for field in self.REQUIRED_SITE_FIELDS:
                if field not in site_data:
                    errors.append(f"[{site_key}] 缺失必填字段: {field}")

            # 检查 author_info
            if 'author_info' in site_data:
                for field in self.REQUIRED_AUTHOR_FIELDS:
                    if field not in site_data['author_info']:
                        errors.append(f"[{site_key}] author_info 缺失: {field}")

            # 检查 persona
            if 'persona' in site_data:
                for field in self.REQUIRED_PERSONA_FIELDS:
                    if field not in site_data['persona']:
                        errors.append(f"[{site_key}] persona 缺失: {field}")

            # 检查 stats
            if 'stats' in site_data:
                for field in self.REQUIRED_STATS_FIELDS:
                    if field not in site_data['stats']:
                        errors.append(f"[{site_key}] stats 缺失: {field}")

            # 检查 articles 中的必填字段
            for article in site_data.get('articles', []):
                if 'slug' not in article:
                    errors.append(f"[{site_key}] 文章缺失 slug")
                if 'title' not in article:
                    errors.append(f"[{site_key}] 文章缺失 title")
                if 'date_published' not in article:
                    errors.append(f"[{site_key}] 文章缺失 date_published")

            # 检查 key 与 domain 一致性
            domain = site_data.get('domain', '')
            if domain and domain != site_key:
                errors.append(f"[{site_key}] key 与 domain 不一致: {domain}")

        if errors and raise_on_error:
            raise ValueError(f"数据校验失败 ({len(errors)} 项):\n" + "\n".join(errors))

        return errors

    def merge_new_articles(self, site_key: str, new_articles: List[Dict[str, Any]]) -> int:
        """
        安全合并新文章到指定站点
        规则：
        - 如果站点不存在 → 自动创建空站点
        - 如果 slug 已存在 → 跳过（保护已有文章）
        - 如果 slug 不存在 → 追加
        - 不修改任何已有文章的数据
        """
        if site_key not in self.data['sites']:
            # 自动创建空站点（首次运行时）
            self.data['sites'][site_key] = {
                'site_name': site_key,
                'domain': site_key,
                'prefix': '',
                'author_info': {},
                'persona': {},
                'articles': [],
                'tools': [],
                'stats': {'total_articles': 0, 'total_tools': 0, 'total_words': 0},
                'updated_at': datetime.now().isoformat(),
                'keyword_map': {},
                'config': {'domain': site_key, 'prefix': ''},
                'location': '',
                'local_events': []
            }
            self.changes_log.append(f"[AUTO_CREATE] 自动创建站点: {site_key}")

        site = self.data['sites'][site_key]
        existing_slugs = {a['slug'] for a in site.get('articles', [])}

        added = 0
        skipped = 0

        for article in new_articles:
            slug = article.get('slug')
            if not slug:
                self.changes_log.append(f"[SKIP] 文章缺少 slug，跳过")
                skipped += 1
                continue

            if slug in existing_slugs:
                self.changes_log.append(f"[PROTECT] slug '{slug}' 已存在，跳过（保护已有数据）")
                skipped += 1
                continue

            # 确保文章有基本字段
            if 'date_published' not in article:
                article['date_published'] = datetime.now().strftime('%Y-%m-%d')
            if 'date_modified' not in article:
                article['date_modified'] = article['date_published']

            site['articles'].append(article)
            existing_slugs.add(slug)
            added += 1

        # 更新 stats
        self._recalc_stats(site_key)
        site['updated_at'] = datetime.now().isoformat()

        self.changes_log.append(f"[ADD] {site_key}: 新增 {added} 篇, 跳过 {skipped} 篇")
        return added

    def merge_new_site(self, site_key: str, site_data: dict):
        """
        安全添加新站点
        规则：如果站点已存在 → 报错（禁止覆盖）
        """
        if site_key in self.data['sites']:
            raise KeyError(f"站点 '{site_key}' 已存在，禁止覆盖。如需更新请使用 merge_new_articles()")

        # 校验新站点数据
        for field in self.REQUIRED_SITE_FIELDS:
            if field not in site_data:
                raise ValueError(f"新站点 '{site_key}' 缺失必填字段: {field}")

        self.data['sites'][site_key] = site_data
        self.changes_log.append(f"[NEW_SITE] 添加新站点: {site_key}")

    def merge_new_tools(self, site_key: str, new_tools: List[Dict[str, Any]]) -> int:
        """
        安全合并新工具
        规则：如果 tool slug/name 已存在 → 跳过
        """
        if site_key not in self.data['sites']:
            # 自动创建空站点（首次运行时）
            self.data['sites'][site_key] = {
                'site_name': site_key,
                'domain': site_key,
                'prefix': '',
                'author_info': {},
                'persona': {},
                'articles': [],
                'tools': [],
                'stats': {'total_articles': 0, 'total_tools': 0, 'total_words': 0},
                'updated_at': datetime.now().isoformat(),
                'keyword_map': {},
                'config': {'domain': site_key, 'prefix': ''},
                'location': '',
                'local_events': []
            }
            self.changes_log.append(f"[AUTO_CREATE] 自动创建站点: {site_key}")

        site = self.data['sites'][site_key]
        existing_tools = {t.get('slug', t.get('name', '')) for t in site.get('tools', [])}

        added = 0
        for tool in new_tools:
            tool_id = tool.get('slug') or tool.get('name', '')
            if tool_id in existing_tools:
                self.changes_log.append(f"[PROTECT] 工具 '{tool_id}' 已存在，跳过")
                continue

            site['tools'].append(tool)
            existing_tools.add(tool_id)
            added += 1

        self._recalc_stats(site_key)
        site['updated_at'] = datetime.now().isoformat()
        self.changes_log.append(f"[ADD_TOOL] {site_key}: 新增 {added} 个工具")
        return added

    def _recalc_stats(self, site_key: str):
        """重新计算站点统计"""
        site = self.data['sites'][site_key]
        articles = site.get('articles', [])
        tools = site.get('tools', [])

        total_words = sum(a.get('word_count', 0) for a in articles)

        site['stats'] = {
            'total_articles': len(articles),
            'total_tools': len(tools),
            'total_words': total_words
        }

    def update_local_events(self, site_key: str, events: List[str]):
        """
        更新 local_events（允许更新，因为这是实时采集的）
        """
        if site_key not in self.data['sites']:
            return

        self.data['sites'][site_key]['local_events'] = events
        self.data['sites'][site_key]['updated_at'] = datetime.now().isoformat()
        self.changes_log.append(f"[UPDATE] {site_key}: 更新 local_events ({len(events)} 条)")

    def update_keyword_map(self, site_key: str, keyword_map: dict):
        """
        更新 keyword_map（允许更新，因为这是采集的）
        """
        if site_key not in self.data['sites']:
            return

        self.data['sites'][site_key]['keyword_map'] = keyword_map
        self.data['sites'][site_key]['updated_at'] = datetime.now().isoformat()
        self.changes_log.append(f"[UPDATE] {site_key}: 更新 keyword_map")

    def save(self, force: bool = False):
        """
        安全保存 global_archive.json

        流程：
        1. 校验数据完整性
        2. 自动备份原文件
        3. 检测是否有非法修改（已有数据被改动）
        4. 写入新文件
        5. 验证写入成功
        """
        # 1. 校验
        self.validate(raise_on_error=True)

        # 2. 备份
        backup_path = self._backup()

        # 3. 检测非法修改（量子防护核心）
        if not force:
            current_data = self._load()
            current_hash = self._compute_hash(current_data)

            if current_hash != self.original_hash:
                # 文件在加载后被外部修改过，需要检查是否是我们的修改
                pass  # 允许，因为我们就是通过这个类修改的

        # 4. 写入
        temp_path = self.filepath + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

        # 5. 原子替换
        os.replace(temp_path, self.filepath)

        # 6. 验证
        with open(self.filepath, 'r', encoding='utf-8') as f:
            verify = json.load(f)
        verify_hash = self._compute_hash(verify)
        saved_hash = self._compute_hash(self.data)

        if verify_hash != saved_hash:
            # 写入失败，恢复备份
            shutil.copy2(backup_path, self.filepath)
            raise RuntimeError(f"写入验证失败！已恢复备份: {backup_path}")

        self.original_hash = verify_hash
        self.changes_log.append(f"[SAVE] 成功保存到: {self.filepath}")

        return {
            'backup': backup_path,
            'changes': self.changes_log,
            'sites_count': len(self.data['sites']),
            'total_articles': sum(len(s['articles']) for s in self.data['sites'].values())
        }

    def get_report(self) -> str:
        """获取操作报告"""
        lines = [
            "=" * 60,
            "Global Archive 量子防护报告",
            "=" * 60,
            f"文件路径: {self.filepath}",
            f"总站点数: {len(self.data['sites'])}",
            f"总文章数: {sum(len(s['articles']) for s in self.data['sites'].values())}",
            "-" * 60,
            "操作日志:",
        ]
        for log in self.changes_log:
            lines.append(f"  {log}")
        lines.append("=" * 60)
        return "\n".join(lines)


def detect_domain():
    cname_path = Path('CNAME')
    if cname_path.exists():
        domain = cname_path.read_text(encoding='utf-8').strip()
        if domain:
            return domain
    archive_path = Path('site_archive.json')
    if archive_path.exists():
        try:
            data = json.loads(archive_path.read_text(encoding='utf-8'))
            domain = data.get('domain')
            if domain:
                if not cname_path.exists():
                    cname_path.write_text(domain, encoding='utf-8')
                return domain
        except:
            pass
    dir_name = Path.cwd().name
    if '.' in dir_name:
        if not cname_path.exists():
            cname_path.write_text(dir_name, encoding='utf-8')
        return dir_name
    domain = f"{dir_name}.org"
    if not cname_path.exists():
        cname_path.write_text(domain, encoding='utf-8')
    return domain


def load_site_archive():
    path = Path('site_archive.json')
    if not verify_checksum(path):
        print(f"   WARN: {path.name} may be corrupted.")
    archive = {}
    if path.exists():
        try:
            archive = json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"   WARN: site_archive.json parse failed: {e}")

    persona_path = Path('persona.json')
    if persona_path.exists():
        try:
            with open(persona_path, 'r', encoding='utf-8') as f:
                persona = json.load(f)
            if persona and persona.get('name'):
                author = archive.get('author', {})
                if not author or not author.get('name'):
                    archive['author'] = persona
                existing_persona = archive.get('persona', {})
                if not existing_persona or not existing_persona.get('name'):
                    archive['persona'] = persona
        except Exception as e:
            print(f"   WARN: persona.json read failed: {e}")

    if not archive.get('prefix'):
        prefix = extract_prefix_from_css()
        if prefix:
            archive['prefix'] = prefix
            print(f"   INFO: prefix synced from style.css: '{prefix}'")

    return archive


CSS_PREFIX_BLACKLIST = {
    'col', 'btn', 'text', 'bg', 'd', 'flex', 'row', 'container', 'nav', 'navbar',
    'card', 'form', 'modal', 'dropdown', 'carousel', 'pagination', 'badge', 'alert',
    'progress', 'list', 'table', 'input', 'label', 'select', 'textarea', 'iframe',
    'embed', 'object', 'canvas', 'svg', 'path', 'rect', 'circle', 'line', 'polyline',
    'polygon', 'g', 'defs', 'clippath', 'mask', 'pattern', 'image', 'use', 'symbol',
    'filter', 'fe', 'stop', 'animate', 'set', 'title', 'desc', 'metadata', 'switch',
    'foreignobject', 'tspan', 'tref', 'textpath', 'altglyph', 'glyph', 'glyphref',
    'mark', 'missing', 'hkern', 'vkern', 'font', 'mpath', 'cursor', 'view', 'script',
    'style', 'link', 'base', 'meta', 'head', 'body', 'html', 'div', 'span', 'p', 'h1',
    'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'thead', 'tbody',
    'tfoot', 'caption', 'colgroup', 'option', 'optgroup', 'button', 'fieldset', 'legend',
    'article', 'section', 'aside', 'header', 'footer', 'main', 'figure', 'figcaption',
    'details', 'summary', 'dialog', 'menu', 'menuitem', 'slot', 'template', 'shadow',
    'content', 'element', 'data', 'time', 'output', 'meter', 'ruby', 'rt', 'rp', 'bdi',
    'bdo', 'wbr', 'cite', 'q', 'dfn', 'abbr', 'address', 'map', 'area', 'param', 'source',
    'track', 'audio', 'video', 'picture', 'del', 'ins', 'small', 'strong', 'em', 'i', 'b',
    'u', 's', 'strike', 'sub', 'sup', 'kbd', 'samp', 'code', 'var', 'pre', 'blockquote',
    'hr', 'br'
}


def extract_prefix_from_css():
    css_path = Path('style.css')
    if not css_path.exists():
        return ''
    try:
        content = css_path.read_text(encoding='utf-8')
        matches = re.findall(r"\.([a-zA-Z]{2,6})-[a-zA-Z]", content)
        if not matches:
            return ''
        filtered = [m.lower() for m in matches if m.lower() not in CSS_PREFIX_BLACKLIST]
        if not filtered:
            return ''
        prefix = Counter(filtered).most_common(1)[0][0]
        return prefix + '-'
    except Exception as e:
        print(f"   WARN: extract_prefix_from_css failed: {e}")
        return ''


def load_global_archive():
    global_path = Path.home() / '.site_builder' / 'archives' / 'global_archive.json'
    if not verify_checksum(global_path):
        print(f"   WARN: {global_path.name} may be corrupted.")
    if global_path.exists():
        try:
            with open(global_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"   WARN: global_archive.json read failed: {e}")
    return {"version": "2.0", "generated_at": "", "sites": {}, "global_people": {}, "global_events": []}


class MetaExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ''
        self.description = ''
        self.date = ''
        self.date_modified = ''
        self.category = ''
        self.author_name = ''
        self.author_job = ''
        self.author_location = ''
        self.og_image = ''
        self.image_paths = []
        self.tool_links = []
        self.in_title = False
        self.in_script = False
        self.in_style = False
        self.date_candidates = []
        self.date_tag_stack = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'script':
            self.in_script = True
        elif tag == 'style':
            self.in_style = True
        elif tag == 'title':
            self.in_title = True
        elif tag == 'meta':
            name = attrs_dict.get('name', '').lower()
            prop = attrs_dict.get('property', '').lower()
            content = attrs_dict.get('content', '')
            if name == 'description':
                self.description = content
            elif name == 'author':
                self.author_name = content
            elif prop == 'article:published_time':
                self.date = content
            elif prop == 'article:modified_time':
                self.date_modified = content
            elif prop == 'article:section':
                self.category = content
            elif prop == 'og:image':
                self.og_image = content
            elif name in ('date', 'published-date', 'publish-date'):
                self.date = content
        elif tag == 'img':
            src = attrs_dict.get('src', '')
            if src and not src.endswith('og-default.jpg'):
                self.image_paths.append(src)
        elif tag == 'a':
            href = attrs_dict.get('href', '')
            if href and '../tools/' in href and href.endswith('.html'):
                slug = href.split('/')[-1].replace('.html', '')
                if slug and slug not in self.tool_links:
                    self.tool_links.append(slug)
        elif tag == 'time' and attrs_dict.get('datetime'):
            self.date = attrs_dict.get('datetime')
        else:
            cls = attrs_dict.get('class', '')
            if cls and any(k in cls.lower() for k in ('date', 'published', 'post-date', 'entry-date', 'meta-date', 'time-stamp', 'publish-date')):
                self.date_tag_stack.append(tag)

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        elif tag == 'script':
            self.in_script = False
        elif tag == 'style':
            self.in_style = False
        elif self.date_tag_stack and tag == self.date_tag_stack[-1]:
            self.date_tag_stack.pop()

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif self.date_tag_stack and not self.in_script and not self.in_style:
            text = data.strip()
            if text and len(text) > 5:
                self.date_candidates.append(text)


def extract_jsonld_author(content):
    author_name = ''
    author_job = ''
    author_location = ''
    try:
        author_match = re.search(r'"@type"\s*:\s*"Person".*?"name"\s*:\s*"([^"]+)"', content, re.DOTALL)
        if author_match:
            author_name = author_match.group(1)
        job_match = re.search(r'"jobTitle"\s*:\s*"([^"]+)"', content, re.DOTALL)
        if job_match:
            author_job = job_match.group(1)
        loc_match = re.search(r'"address"\s*:\s*\{[^}]*"addressLocality"\s*:\s*"([^"]+)"', content, re.DOTALL)
        if loc_match:
            author_location = loc_match.group(1)
    except:
        pass
    return author_name, author_job, author_location


def parse_date(date_str):
    """Parse multiple date formats to datetime (input compatible with Chinese, output forced English)"""
    if not date_str:
        return None
    date_str = date_str.strip()

    # Compatible with Chinese date input (e.g. 2026\u5e747\u670812\u65e5) -> convert to English output
    cn_match = re.match(r'(\d{4})\u5e74(\d{1,2})\u6708(\d{1,2})\u65e5', date_str)
    if cn_match:
        year, month, day = cn_match.groups()
        return datetime(int(year), int(month), int(day))

    formats = [
        '%B %d, %Y',
        '%b %d, %Y',
        '%Y-%m-%d',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None




# V3.13: REMOVED generate_random_past_date() and set_site_date_range()
# build.py NEVER generates dates. Dates MUST exist in HTML.
# If HTML has no date -> article is SKIPPED (not added to articles.json)

def extract_article_info(html_path):
    try:
        content = html_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"     WARN: read failed: {html_path.name} -- {e}")
        return None

    try:
        extractor = MetaExtractor()
        extractor.feed(content)
    except:
        pass

    title = extractor.title.strip()
    # V3.10 
    title = re.sub(r'\s*[-|–—]\s*[a-zA-Z0-9-]+\.(com|org|net|io|co)\s*$', '', title, flags=re.IGNORECASE).strip()
    if not title:
        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', content, re.DOTALL | re.IGNORECASE)
        if h1_match:
            title = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()
            title = re.sub(r'\s*[-|–—]\s*[a-zA-Z0-9-]+\.(com|org|net|io|co)\s*$', '', title, flags=re.IGNORECASE).strip()

    excerpt = extractor.description.strip()
    if not excerpt:
        p_match = re.search(r'<p[^>]*>(.*?)</p>', content, re.DOTALL | re.IGNORECASE)
        if p_match:
            excerpt = re.sub(r'<[^>]+>', '', p_match.group(1)).strip()[:200]

    # ========== （V3.8 ）==========
    date_raw = extractor.date.strip()
    if not date_raw and extractor.date_candidates:
        date_raw = extractor.date_candidates[0]

    # ： MetaExtractor 
    if not date_raw:
        clean_content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        clean_content = re.sub(r'<style[^>]*>.*?</style>', '', clean_content, flags=re.DOTALL | re.IGNORECASE)

        contextual_patterns = [
            r'(?:Published|Posted|Date|Updated)[:on\s]+(\d{4}-\d{2}-\d{2})',
            r'(?:Published|Posted|Date|Updated)[:on\s]+((?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})',
            r'(?:Published|Posted|Date|Updated)[:on\s]+(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})',
        ]
        for pattern in contextual_patterns:
            m = re.search(pattern, clean_content, re.IGNORECASE)
            if m:
                date_raw = m.group(1)
                break

        if not date_raw:
            time_match = re.search(r'<time[^>]*datetime="([^"]+)"', clean_content, re.IGNORECASE)
            if time_match:
                date_raw = time_match.group(1)

        if not date_raw:
            date_tag_match = re.search(r'<(?:span|div|p|em|small|time)[^>]*class="[^"]*(?:date|published|post-date|entry-date)[^"]*"[^>]*>([^<]+)</(?:span|div|p|em|small|time)>', clean_content, re.IGNORECASE)
            if date_tag_match:
                date_raw = date_tag_match.group(1).strip()

        if not date_raw:
            general_patterns = [
                r'(\d{4}-\d{2}-\d{2})',
                r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})',
            ]
            for pattern in general_patterns:
                m = re.search(pattern, clean_content, re.IGNORECASE)
                if m:
                    date_raw = m.group(1)
                    break

    # Force English date format - reject any Chinese characters in output
    date_published = None
    date_iso = None
    if date_raw:
        dt = parse_date(date_raw)
        if dt:
            date_iso = dt.strftime("%Y-%m-%d")         # ISO: 2026-07-15 (for sorting)
            date_published = dt.strftime("%B %d, %Y")  # English: July 15, 2026
        else:
            # V3.13: Chinese date in HTML is invalid - skip article
            if re.search(r'[一-鿿]', str(date_raw)):
                print(f"     SKIP {html_path.stem}: Chinese date detected in HTML")
                return None
            else:
                date_published = date_raw
                date_iso = date_raw
    #  date  date_published 
    date = date_published if date_published else date_raw

    category = extractor.category.strip()
    if not category:
        cat_match = re.search(r'"articleSection"\s*:\s*"([^"]+)"', content)
        if cat_match:
            category = cat_match.group(1)

    #  tags（ meta keywords  JSON-LD）
    tags = []
    keywords_match = re.search(r'<meta[^>]*name="keywords"[^>]*content="([^"]*)"', content, re.IGNORECASE)
    if keywords_match:
        tags = [t.strip() for t in keywords_match.group(1).split(',') if t.strip()]
    if not tags:
        #  JSON-LD 
        tags_match = re.search(r'"keywords"\s*:\s*\[(.*?)\]', content, re.DOTALL)
        if tags_match:
            tags_str = tags_match.group(1)
            tags = [t.strip().strip('"').strip("'") for t in tags_str.split(',') if t.strip()]
    if not tags and category:
        tags = [category]

    author_name, author_job, author_location = extract_jsonld_author(content)
    if not author_name and extractor.author_name:
        author_name = extractor.author_name

    has_image = len(extractor.image_paths) > 0 or bool(extractor.og_image)
    image_path = extractor.og_image
    if not image_path and extractor.image_paths:
        image_path = extractor.image_paths[0]

    try:
        text = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        word_count = len(text.split())
    except:
        word_count = 0

    read_time = f"{max(1, word_count // 200)}min read"

    #  excerpt （ description，200）
    excerpt_final = excerpt or title[:200]

    return {
        'slug': html_path.stem,
        'title': title or html_path.stem.replace('-', ' ').title(),
        'description': excerpt,
        'excerpt': excerpt_final,
        'category': category,
        'tags': tags,
        'date_published': date_published,
        'date': date,
        'date_modified': extractor.date_modified,
        'word_count': word_count,
        'has_image': has_image,
        'image_path': image_path,
        'tools_referenced': extractor.tool_links,
        'author': {
            'name': author_name,
            'job_title': author_job,
            'location': author_location
        },
        'date_iso': date_iso,
        'readTime': read_time
    }


def scan_blog_articles():
    blog_dir = Path('blog')
    if not blog_dir.exists():
        print("   WARN: blog/ dir not found, skip")
        return []

    html_files = sorted([f for f in blog_dir.iterdir()
                         if f.suffix in ('.html', '.txt')
                         and f.name not in ('index.html', 'post.html', 'article.html')
                         and not f.name.startswith('.')
                         and '.bak' not in f.name])

    if not html_files:
        print("   WARN: no article files in blog/")
        return []

    existing_articles = load_articles()
    existing_map = {a.get('slug', ''): a for a in existing_articles if isinstance(a, dict) and a.get('slug')}

    articles = []
    new_count = 0
    update_count = 0

    for html_path in html_files:
        slug = html_path.stem
        info = extract_article_info(html_path)
        if not info:
            continue

        if slug in existing_map:
            # V3.13: EXISTING ARTICLES ARE COMPLETELY PROTECTED.
            # build.py NEVER modifies existing articles. Only scans and appends new ones.
            old = existing_map[slug]
            articles.append(old)
            print(f"     KEEP {slug} -- {old.get('title', '')[:40]}... (existing, protected)")
        else:
            # V3.13: NEW ARTICLE - must have date from HTML. extract_article_info already returned None if missing.
            # Double-check here as safety.
            if not info.get('date_published') or not info.get('date_iso'):
                print(f"     SKIP {slug}: no date in HTML, cannot add new article")
                continue
            # Ensure one date per article
            info['date'] = info['date_published']
            if not info.get('date_modified'):
                info['date_modified'] = info['date_iso']
            #  tags  excerpt
            if not info.get('tags'):
                info['tags'] = [info.get('category', 'General')] if info.get('category') else ['General']
            if not info.get('excerpt') and info.get('description'):
                info['excerpt'] = info['description'][:200]
            articles.append(info)
            new_count += 1
            print(f"     NEW {slug} -- {info['title'][:40]}... ({info['word_count']} words) [{info['date_published']}]")

    # V3.13: Keep all existing articles that are still in articles.json
    # (They were already added above via existing_map. No need to re-add here.)
    # If an article exists in articles.json but HTML is gone, we KEEP it in articles.json
    # (Manual cleanup only - build.py does not delete articles)

    # Sort by date_iso for consistent ordering across all pages
    def _sort_key(article):
        date_iso = article.get('date_iso', '')
        if date_iso and re.match(r'\d{4}-\d{2}-\d{2}', str(date_iso)):
            return date_iso
        dp = article.get('date_published', '')
        dt = parse_date(dp)
        if dt:
            return dt.strftime('%Y-%m-%d')
        return '1970-01-01'

    articles.sort(key=_sort_key, reverse=True)
    atomic_write_json('articles.json', articles, indent=2, ensure_ascii=False)
    print(f"   OK articles.json updated ({len(articles)} articles, {new_count} new, {update_count} updated)")
    return articles


def load_articles():
    path = Path('articles.json')
    if not verify_checksum(path):
        print(f"   WARN: {path.name} may be corrupted.")
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except:
        return []

    if isinstance(data, list):
        articles = data
    elif isinstance(data, dict):
        for key in ['articles', 'posts', 'items', 'data']:
            if key in data and isinstance(data[key], list):
                articles = data[key]
                break
        else:
            articles = []
            for k, v in data.items():
                if isinstance(v, dict):
                    v['slug'] = v.get('slug', k)
                    articles.append(v)
    else:
        return []

    #  + 
    filtered = []
    for item in articles:
        if not isinstance(item, dict):
            continue
        item.pop('url', None)
        slug = item.get('slug', '')
        title = item.get('title', '')
        desc = item.get('description', '')
        if slug in ('article', 'post', 'template', 'placeholder', '') or '{' in str(title) or '{' in str(desc):
            continue
        if not (item.get('date') or item.get('date_published')):
            continue
        filtered.append(item)

    # Sort by date_iso (YYYY-MM-DD) for consistent ordering
    def get_sort_date(a):
        date_iso = a.get('date_iso', '')
        if date_iso and re.match(r'\d{4}-\d{2}-\d{2}', str(date_iso)):
            return date_iso
        dp = a.get('date_published', '')
        dt = parse_date(dp)
        if dt:
            return dt.strftime('%Y-%m-%d')
        return '1970-01-01'

    filtered.sort(key=get_sort_date, reverse=True)
    return filtered


def get_tool_pages(archive):
    tools = archive.get('tools', [])
    if not isinstance(tools, list):
        print("   WARN: tools field is not a list")
        return []
    result = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        slug = tool.get('slug', '')
        title = tool.get('title', slug.replace('-', ' ').title())
        if slug:
            result.append((slug, title))
    return result


def get_compliance_pages():
    pages = []
    for fname in ['about.html', 'contact.html', 'faq.html', 'privacy.html',
                    'terms.html', 'disclaimer.html', 'disclosure.html']:
        if Path(fname).exists():
            pages.append(fname)
    return pages


def is_placeholder_article(article):
    if not isinstance(article, dict):
        return True
    slug = article.get('slug', '')
    title = article.get('title', '')
    desc = article.get('description', '')
    if slug in ('article', 'post', 'template', 'placeholder'):
        return True
    if any(marker in title for marker in ['{OG_TITLE}', '{TITLE}', '{DATE']):
        return True
    if any(marker in desc for marker in ['{OG_DESCRIPTION}', '{DESCRIPTION}', '{META_DESC}', '{EXCERPT}']):
        return True
    placeholder_patterns = [
        r'\{[A-Z_]+\}', r'\{\{.*?\}\}', r'\[%\s*.*?\s*%\]',
        r'PLACEHOLDER_[A-Z_]+', r'Lorem\s+ipsum', r'TODO|FIXME|XXX|HACK',
    ]
    text_to_check = f"{title} {desc}"
    for pattern in placeholder_patterns:
        if re.search(pattern, text_to_check, re.IGNORECASE):
            return True
    return False


# ═══════════════════════════════════════════════════
# V3.6 ： + 
# ═══════════════════════════════════════════════════

def find_all_card_blocks(inner_html):
    cards = []

    # 1： <article> 
    article_starts = list(re.finditer(r'<article\b[^>]*>', inner_html))
    for start_match in article_starts:
        start_pos = start_match.start()
        depth = 1
        pos = start_match.end()
        while pos < len(inner_html) and depth > 0:
            next_open_match = re.search(r'<article\b', inner_html[pos:])
            next_open = pos + next_open_match.start() if next_open_match else -1
            next_close = inner_html.find('</article>', pos)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                pos = next_open + 8
            else:
                depth -= 1
                pos = next_close + 10

        if depth == 0:
            end_pos = pos
            card_html = inner_html[start_pos:end_pos]
            if 'href=' in card_html and len(card_html) > 100:
                cards.append((start_pos, end_pos, card_html))

    # 2： <a> 
    if not cards:
        a_starts = list(re.finditer(r'<a\b[^>]*>', inner_html))
        for i, start_match in enumerate(a_starts):
            start_pos = start_match.start()
            depth = 1
            pos = start_match.end()
            while pos < len(inner_html) and depth > 0:
                next_open_match = re.search(r'<a\b', inner_html[pos:])
                next_open = pos + next_open_match.start() if next_open_match else -1
                next_close = inner_html.find('</a>', pos)
                if next_close == -1:
                    break
                if next_open != -1 and next_open < next_close:
                    depth += 1
                    pos = next_open + 2
                else:
                    depth -= 1
                    pos = next_close + 4

            if depth == 0:
                end_pos = pos
                card_html = inner_html[start_pos:end_pos]
                has_img = '<img' in card_html
                has_heading = bool(re.search(r'<h[1-6][^>]*>', card_html))
                has_title_class = 'title' in card_html.lower() or 'heading' in card_html.lower()
                has_content = 'href=' in card_html and len(re.sub(r'<[^>]+>', '', card_html).strip()) > 20
                if has_img or has_heading or has_title_class or has_content:
                    cards.append((start_pos, end_pos, card_html))

    return cards


def extract_card_template(card_html):
    template = card_html

    href_match = re.search(r'href="([^"]*)"', template)
    if href_match:
        template = template[:href_match.start(1)] + '{HREF}' + template[href_match.end(1):]

    src_match = re.search(r'src="([^"]*)"', template)
    if src_match:
        template = template[:src_match.start(1)] + '{IMG_SRC}' + template[src_match.end(1):]

    alt_match = re.search(r'alt="([^"]*)"', template)
    if alt_match:
        template = template[:alt_match.start(1)] + '{ALT}' + template[alt_match.end(1):]

    emoji_match = re.search(r'(<div[^>]*class="[^"]*(?:blog-card-img|tool-card-icon)[^"]*"[^>]*>)([^<\s][^<]*)(</div>)', template)
    if emoji_match:
        emoji_content = emoji_match.group(2).strip()
        if emoji_content and len(emoji_content) <= 10:
            template = template[:emoji_match.start(2)] + '{EMOJI}' + template[emoji_match.end(2):]

    title_patterns = [
        r'(<h[1-6][^>]*>)(.*?)(</h[1-6]>)',
        r'(<[^>]*\bclass="[^"]*(?:title|heading)[^"]*"[^>]*>)(.*?)(</[^>]+>)',
    ]
    for pattern in title_patterns:
        title_match = re.search(pattern, template, re.S)
        if title_match:
            template = template[:title_match.start(2)] + '{TITLE}' + template[title_match.end(2):]
            break

    excerpt_patterns = [
        r'(<[^>]*\bclass="[^"]*(?:excerpt|desc|summary|snippet)[^"]*"[^>]*>)(.*?)(</[^>]+>)',
        r'(<p[^>]*>)(.*?)(</p>)',
    ]
    for pattern in excerpt_patterns:
        excerpt_match = re.search(pattern, template, re.S)
        if excerpt_match:
            template = template[:excerpt_match.start(2)] + '{EXCERPT}' + template[excerpt_match.end(2):]
            break

    date_patterns = [
        r'(<(?:span|div|time)[^>]*\bclass="[^"]*(?:meta|date|time|info)[^"]*"[^>]*>)(.*?)(</(?:span|div|time)>)',
        r'(<span[^>]*>)(.*?)(</span>)',
    ]
    for pattern in date_patterns:
        date_match = re.search(pattern, template, re.S)
        if date_match:
            old_text = date_match.group(2).strip()
            if re.search(r'\w{3}\s+\d{1,2}|\d{4}-\d{2}-\d{2}|\ud83d\udcc5|||', old_text):
                template = template[:date_match.start(2)] + '{DATE}' + template[date_match.end(2):]
                break

    return template


def apply_article_to_template(template, article, href_prefix, img_prefix):
    slug = article.get('slug', '')
    title = article.get('title', slug)
    excerpt = article.get('description', '')
    if excerpt:
        excerpt = excerpt[:150] + '...' if len(excerpt) > 150 else excerpt

    # Use date_published directly (already forced to English format)
    date_str = article.get('date_published', '')
    if not date_str:
        date_iso = article.get('date_iso', '')
        if date_iso:
            try:
                dt = datetime.strptime(date_iso, '%Y-%m-%d')
                date_str = dt.strftime('%B %d, %Y')
            except:
                date_str = date_iso

    new_href = f'{href_prefix}{slug}.html'
    new_img_tag = f'<img src="{img_prefix}{slug}.jpg" alt="{title}" width="400" height="225" loading="lazy" style="width:100%;height:100%;object-fit:cover;border-radius:12px 12px 0 0;">'
    new_img_src = f'{img_prefix}{slug}.jpg'
    safe_title = title.replace('\\', '\\\\').replace('"', '\"')
    safe_excerpt = excerpt.replace('\\', '\\\\').replace('"', '\"')
    safe_date = date_str.replace('\\', '\\\\').replace('"', '\"')

    result = template
    result = result.replace('{HREF}', new_href)

    if '{IMG_SRC}' in result:
        result = result.replace('{IMG_SRC}', new_img_src)

    if '{ALT}' in result:
        result = result.replace('{ALT}', safe_title)

    if '{EMOJI}' in result:
        result = result.replace('{EMOJI}', new_img_tag)
    elif '<img' not in result and 'ft-blog-card-img' in result:
        result = result.replace(
            '<div class="ft-blog-card-img">',
            f'<div class="ft-blog-card-img">{new_img_tag}'
        )

    result = result.replace('{TITLE}', safe_title)
    result = result.replace('{EXCERPT}', safe_excerpt)
    result = result.replace('{DATE}', safe_date)

    return result


def clean_duplicate_markers(content, start_marker, end_marker, prefix='', file_name=''):
    start_pat = rf'<!--\s*{re.escape(start_marker)}\s*-->'
    end_pat = rf'<!--\s*{re.escape(end_marker)}\s*-->'

    starts = list(re.finditer(start_pat, content))
    ends = list(re.finditer(end_pat, content))

    if len(starts) == 1 and len(ends) == 1:
        return content

    if len(starts) == 0 or len(ends) == 0:
        print(f"   WARN: {file_name}  (START={len(starts)}, END={len(ends)})")
        return content

    print(f"   WARN: {file_name}  (START={len(starts)}, END={len(ends)})，...")

    first_start = starts[0]
    last_end = ends[-1]
    before = content[:first_start.start()]
    after = content[last_end.end():]

    first_end = ends[0]
    middle = content[first_start.end():first_end.start()]

    container_open = ''
    container_close = ''

    before_divs = list(re.finditer(r'<div\b[^>]*>', before))
    before_closes = list(re.finditer(r'</div>', before))
    if before_divs and len(before_divs) > len(before_closes):
        container_open = before_divs[-1].group(0)
        container_close = '</div>'

    if not container_open:
        container_match = re.search(r'^(\s*)(<div\b[^>]*>)(.*)(</div>)(\s*)$', middle, re.S)
        if container_match:
            container_open = container_match.group(2)
            container_close = container_match.group(4)
        else:
            div_start = re.search(r'<div\b[^>]*>', middle)
            div_end_match = list(re.finditer(r'</div>', middle))
            if div_start and div_end_match:
                container_open = div_start.group(0)
                container_close = '</div>'

    if container_open and container_close:
        clean_inner = f'\n{container_open}\n{container_close}\n'
    else:
        grid_class = f'{prefix}card-grid' if prefix else 'card-grid'
        clean_inner = f'\n<div class="{grid_class}">\n</div>\n'

    return before + first_start.group(0) + clean_inner + last_end.group(0) + after


def update_single_homepage(hp_path, start_marker, end_marker, articles, prefix, href_prefix, img_prefix, is_blog=False):
    if not hp_path.exists():
        return False, f"{hp_path.name} "

    try:
        original_content = hp_path.read_text(encoding='utf-8')

        cleaned_content = clean_duplicate_markers(original_content, start_marker, end_marker, prefix, hp_path.name)
        if cleaned_content != original_content:
            original_content = cleaned_content
            atomic_write_text(hp_path, cleaned_content, encoding='utf-8')
            print(f"   INFO: {hp_path.name} ")

        mid_marker = 'BLOG_ARTICLES_MID'
        if is_blog and mid_marker in original_content:
            mid_marker_full = f'<!-- {mid_marker} -->'
            cleaned_content = original_content.replace(mid_marker_full, '')
            if cleaned_content != original_content:
                atomic_write_text(hp_path, cleaned_content, encoding='utf-8')
                print(f"   INFO: {hp_path.name} BLOG_MID marker removed, unified processing")
            return _update_homepage_section(
                hp_path, start_marker, end_marker, articles,
                prefix, href_prefix, img_prefix,
                original_content=cleaned_content, is_blog=True, keep_old_cards=False
            )

        return _update_homepage_section(
            hp_path, start_marker, end_marker, articles,
            prefix, href_prefix, img_prefix,
            original_content=original_content, is_blog=is_blog, keep_old_cards=False
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, str(e)


def _update_homepage_section(hp_path, start_marker, end_marker, articles, prefix, href_prefix, img_prefix, original_content, is_blog=False, dry_run=False, keep_old_cards=True):
    pattern = rf'(<!--\s*{re.escape(start_marker)}\s*-->)(.*?)(<!--\s*{re.escape(end_marker)}\s*-->)'
    match = re.search(pattern, original_content, re.S)

    if not match:
        return False, f" {start_marker}"

    inner = match.group(2)
    inner_original = inner

    # V3.10-fix: remove stale blog_mid ad slots to prevent accumulation
    if is_blog:
        inner = re.sub(r'<div\s+class="[^"]*ad-slot[^"]*"[^>]*data-ad-slot="blog_mid"[^>]*>.*?</div>', '', inner, flags=re.S)

    old_cards = find_all_card_blocks(inner)

    if not old_cards:
        return False, f"，（ <a> ）"

    first_card_html = old_cards[0][2]
    template = extract_card_template(first_card_html)

    required_placeholders = ['{HREF}']
    missing = [p for p in required_placeholders if p not in template]
    if missing:
        return False, f"，: {missing}，"

    if '{TITLE}' not in template and '{EXCERPT}' not in template:
        return False, f"，， HTML"

    new_slug = articles[0].get('slug', '') if articles else ''
    all_cards_html = []

    new_slugs = set()
    if articles:
        for article in articles:
            new_card = apply_article_to_template(template, article, href_prefix, img_prefix)
            all_cards_html.append(new_card)
            new_slugs.add(article.get('slug', ''))

    if keep_old_cards:
        for start_pos, end_pos, card_html in old_cards:
            old_href_match = re.search(r'href="([^"]+)"', card_html)
            if old_href_match:
                old_slug = old_href_match.group(1).split('/')[-1].replace('.html', '')
                if old_slug in new_slugs:
                    continue
            all_cards_html.append(card_html)

    if is_blog:
        kept_cards = all_cards_html[:]
        # V3.10 ：，
        article_only_cards = [c for c in kept_cards if 'ad-slot' not in c]
        ad_slots_needed = len(article_only_cards) // 9
        for i in range(ad_slots_needed, 0, -1):
            insert_pos = i * 9
            # 9/18/27... kept_cards 
            actual_pos = 0
            article_count = 0
            for idx, card in enumerate(kept_cards):
                if 'ad-slot' not in card:
                    article_count += 1
                    if article_count == insert_pos:
                        actual_pos = idx + 1
                        break
            if actual_pos > 0:
                ad_html = f'<div class="{prefix}ad-slot has-content" data-ad-slot="blog_mid" style="width:100%;height:250px;margin:20px 0;grid-column:1/-1;"></div>'
                kept_cards.insert(actual_pos, ad_html)
    else:
        max_count = 6
        kept_cards = all_cards_html[:max_count]

    first_card_start = old_cards[0][0]
    last_card_end = old_cards[-1][1]

    before_cards = inner[:first_card_start]
    after_cards = inner[last_card_end:]

    cards_html = '\n'.join(kept_cards)
    new_inner = before_cards + cards_html + after_cards

    if new_inner == inner_original:
        return True, ""

    new_content = original_content[:match.start(2)] + '\n' + new_inner + '\n' + original_content[match.end(2):]

    if dry_run:
        return True, new_inner

    atomic_write_text(hp_path, new_content, encoding='utf-8')

    card_count = sum(1 for c in kept_cards if c.strip().startswith('<a '))
    is_valid, verify_msg = verify_homepage(
        hp_path, start_marker, end_marker, original_content,
        prefix=prefix, expected_count=card_count, is_blog=is_blog
    )
    if not is_valid:
        atomic_write_text(hp_path, original_content, encoding='utf-8')
        return False, f"，: {verify_msg[:300]}"

    return True, f" {len(kept_cards)} （+，）"


def verify_homepage(hp_path, start_marker, end_marker, original_content, prefix='', expected_count=0, is_blog=False):
    try:
        new_content = hp_path.read_text(encoding='utf-8')
    except Exception as e:
        return False, f": {e}"

    start_pat = rf'<!--\s*{re.escape(start_marker)}\s*-->'
    end_pat = rf'<!--\s*{re.escape(end_marker)}\s*-->'

    orig_start_count = len(re.findall(start_pat, original_content))
    orig_end_count = len(re.findall(end_pat, original_content))
    new_start_count = len(re.findall(start_pat, new_content))
    new_end_count = len(re.findall(end_pat, new_content))

    if orig_start_count != 1 or orig_end_count != 1:
        return False, f""
    if new_start_count != 1 or new_end_count != 1:
        return False, f""

    orig_start_match = re.search(start_pat, original_content)
    orig_end_match = re.search(end_pat, original_content)
    new_start_match = re.search(start_pat, new_content)
    new_end_match = re.search(end_pat, new_content)

    orig_before = original_content[:orig_start_match.start()]
    new_before = new_content[:new_start_match.start()]
    orig_after = original_content[orig_end_match.end():]
    new_after = new_content[new_end_match.end():]

    if _norm(orig_before) != _norm(new_before) or _norm(orig_after) != _norm(new_after):
        return False, f""

    inner_new = new_content[new_start_match.end():new_end_match.start()]

    cards = find_all_card_blocks(inner_new)
    actual_cards = len(cards)
    if actual_cards != expected_count:
        return False, f":  {expected_count},  {actual_cards}"

    if is_blog:
        ad_pattern = rf'<div\s+class="{re.escape(prefix)}ad-slot\s+has-content"'
        ad_count = len(re.findall(ad_pattern, inner_new))
        article_cards_in_inner = sum(1 for c in find_all_card_blocks(inner_new) if 'ad-slot' not in c[2])
        # blog_mid: >=9 >=1
        if article_cards_in_inner >= 9 and ad_count < 1:
            return False, f"blog_mid ad slot missing: expected at least 1, found {ad_count}"

    return True, ""


def _norm(t):
    return t.replace('\r\n', '\n').replace('\r', '\n').strip()


def update_three_homepages(articles, prefix):
    if not articles:
        print("   WARN: no articles to update homepages")
        return

    index_articles = articles[:6]
    tools_articles = articles[6:12]
    blog_articles = articles[:]

    updates = [
        (Path('index.html'), 'INDEX_ARTICLES_START', 'INDEX_ARTICLES_END',
         index_articles, './blog/', './images/blog/', False),
        (Path('blog/index.html'), 'BLOG_ARTICLES_START', 'BLOG_ARTICLES_END',
         blog_articles, './', '../images/blog/', True),
        (Path('tools/index.html'), 'TOOLS_ARTICLES_START', 'TOOLS_ARTICLES_END',
         tools_articles, '../blog/', '../images/blog/', False),
    ]

    success_count = 0
    for hp_path, start, end, arts, href_p, img_p, is_blog in updates:
        ok, msg = update_single_homepage(hp_path, start, end, arts, prefix, href_p, img_p, is_blog)
        if ok:
            print(f"   OK {hp_path}: {msg}")
            success_count += 1
        else:
            print(f"   WARN {hp_path}: {msg}")

    print(f"   OK three homepages updated ({success_count}/3)")


def build_sitemap(domain, articles, tools, compliance):
    today = datetime.now().strftime('%Y-%m-%d')
    base = f"https://{domain}/"
    urls = [
        (base, '1.0', 'daily', today),
        (f"{base}tools/", '0.8', 'weekly', today),
        (f"{base}blog/", '0.8', 'weekly', today),
    ]
    for a in articles:
        if isinstance(a, dict) and 'slug' in a and not is_placeholder_article(a):
            article_date = a.get('date_modified', '') or a.get('date_published', '') or today
            urls.append((f"{base}blog/{a['slug']}.html", '0.7', 'monthly', article_date))
    for slug, _ in tools:
        urls.append((f"{base}tools/{slug}.html", '0.8', 'monthly', today))
    for p in compliance:
        urls.append((f"{base}{p}", '0.3', 'monthly', today))
    urls.append((f"{base}sitemap.html", '0.5', 'monthly', today))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, priority, changefreq, lastmod in urls:
        lines.append(f'  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod><changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>')
    lines.append('</urlset>')
    return '\n'.join(lines)


def build_sitemap_html(domain, articles, tools, compliance):
    base = f"https://{domain}/"
    lines = [
        '<!DOCTYPE html>', '<html lang="en">', '<head>',
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        '<title>Sitemap | ' + domain + '</title>',
        '<meta name="description" content="Sitemap for ' + domain + ' - all pages, tools, and articles.">',
        '<link rel="canonical" href="' + base + 'sitemap.html">',
        '<link rel="stylesheet" href="style.css">',
        '<meta property="og:title" content="Sitemap | ' + domain + '">',
        '<meta property="og:description" content="Sitemap for ' + domain + ' - all pages, tools, and articles.">',
        '<meta property="og:type" content="website">',
        '<meta property="og:url" content="' + base + 'sitemap.html">',
        '<meta property="og:image" content="' + base + 'images/og-default.jpg">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="Sitemap | ' + domain + '">',
        '<meta name="twitter:description" content="Sitemap for ' + domain + ' - all pages, tools, and articles.">',
        '<meta name="twitter:image" content="' + base + 'images/og-default.jpg">',
        '</head>', '<body>',
        '<header class="site-header">',
        '  <div class="container"><a href="./index.html" class="logo">' + domain + '</a></div>',
        '</header>',
        '<main class="container" style="padding:40px 20px;">',
        '<h1>Sitemap</h1>',
        '<h2>Main Pages</h2>', '<ul>',
        '<li><a href="' + base + '">Home</a></li>',
        '<li><a href="' + base + 'tools/">Tools</a></li>',
        '<li><a href="' + base + 'blog/">Blog</a></li>',
    ]
    for p in compliance:
        name = p.replace('.html', '').replace('-', ' ').title()
        lines.append(f'<li><a href="{base}{p}">{name}</a></li>')
    lines.append('<li><a href="' + base + 'sitemap.html">Sitemap</a></li>')
    lines.append('</ul>')
    lines.append('<h2>Tools</h2><ul>')
    for slug, title in tools:
        lines.append(f'<li><a href="{base}tools/{slug}.html">{html.escape(title)}</a></li>')
    lines.append('</ul>')
    lines.append('<h2>Blog Articles</h2><ul>')
    for a in articles:
        if isinstance(a, dict) and 'slug' in a and 'title' in a and not is_placeholder_article(a):
            safe_title = html.escape(a["title"])
            lines.append(f'<li><a href="{base}blog/{a["slug"]}.html">{safe_title}</a></li>')
    lines.append('</ul>')
    lines.append('</main></body></html>')
    return '\n'.join(lines)


def build_llms(domain, articles, tools, archive):
    base = f"https://{domain}/"
    site_name = archive.get('site_name', domain) if isinstance(archive, dict) else domain
    lines = [
        f'# {site_name}', '',
        f'Free online calculators and educational content. Not professional advice.',
        '', '## Pages',
        f'- Home: {base}', f'- Tools: {base}tools/', f'- Blog: {base}blog/',
        f'- About: {base}about.html', f'- Contact: {base}contact.html',
        f'- FAQ: {base}faq.html', f'- Sitemap: {base}sitemap.html',
        '', '## Tools',
    ]
    for slug, title in tools:
        lines.append(f'- {title}: {base}tools/{slug}.html')
    lines.extend(['', '## Articles'])
    for a in articles:
        if isinstance(a, dict) and not is_placeholder_article(a):
            excerpt = a.get('description', '')
            date = a.get('date_published', '')
            lines.append(f'- {a.get("title", "")} ({a.get("slug", "")}) -- {excerpt} -- {date}')
    return '\n'.join(lines)


def build_robots(domain):
    return f"""User-agent: *
Allow: /
Sitemap: https://{domain}/sitemap.xml
"""


def merge_articles(existing_articles, new_articles):
    existing_map = {a.get('slug'): a for a in existing_articles if isinstance(a, dict) and a.get('slug')}
    merged = []
    seen = set()
    for new_art in new_articles:
        if not isinstance(new_art, dict):
            continue
        slug = new_art.get('slug')
        if not slug or slug in seen:
            continue
        if is_placeholder_article(new_art):
            continue
        seen.add(slug)
        if slug in existing_map:
            old = dict(existing_map[slug])
            for key, val in new_art.items():
                if val is not None or key not in old:
                    old[key] = val
            #  tags  excerpt
            if not old.get('tags') and old.get('category'):
                old['tags'] = [old['category']]
            if not old.get('excerpt') and old.get('description'):
                old['excerpt'] = old['description'][:200]
            merged.append(old)
        else:
            #  tags  excerpt
            if not new_art.get('tags') and new_art.get('category'):
                new_art['tags'] = [new_art['category']]
            if not new_art.get('excerpt') and new_art.get('description'):
                new_art['excerpt'] = new_art['description'][:200]
            merged.append(new_art)
    for slug, old_art in existing_map.items():
        if slug not in seen and not is_placeholder_article(old_art):
            merged.append(old_art)
    return merged


def merge_tools(existing_tools, new_tools):
    existing_map = {t.get('slug'): t for t in existing_tools if isinstance(t, dict) and t.get('slug')}
    merged = []
    seen = set()
    for new_tool in new_tools:
        if not isinstance(new_tool, dict):
            continue
        slug = new_tool.get('slug')
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if slug in existing_map:
            old = dict(existing_map[slug])
            for key, val in new_tool.items():
                if val is not None or key not in old:
                    old[key] = val
            merged.append(old)
        else:
            merged.append(new_tool)
    for slug, old_tool in existing_map.items():
        if slug not in seen:
            merged.append(old_tool)
    return merged


def update_site_archive(archive, articles):
    if not archive or not isinstance(archive, dict):
        return
    existing_articles = archive.get('articles', [])
    if not isinstance(existing_articles, list):
        existing_articles = []
    merged = merge_articles(existing_articles, articles)
    archive['articles'] = merged
    archive['article_count'] = len(merged)
    archive['word_count'] = sum(a.get('word_count', 0) for a in merged)
    archive['generated_at'] = datetime.now().isoformat()


def deep_merge_dict(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override if override is not None else base
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge_dict(result[key], val)
        elif val is not None or key not in result:
            result[key] = val
    return result


def sync_global_archive(archive, domain):
    """
    Quantum-Protected Global Archive Sync (V2.0 - using GlobalGuard)
    Rules:
    1. ONLY append new articles (by slug), NEVER modify existing articles
    2. ONLY append new tools (by slug), NEVER modify existing tools
    3. NEVER modify site static fields: config, persona, keyword_map, author_info, domain, prefix, location
    4. ONLY update: stats, updated_at, articles list (append-only), tools list (append-only)
    5. If conflict detected: print QUANTUM BLOCK and keep old value
    6. Auto-backup + validate + atomic write via GlobalGuard
    """
    global_path = Path.home() / '.site_builder' / 'archives' / 'global_archive.json'

    # 使用内嵌的 GlobalGuard 进行量子防护写入
    try:
        guard = GlobalGuard(str(global_path))
    except FileNotFoundError:
        print("   WARN: global_archive.json not found, creating new one")
        guard_data = {
            "version": "2.0",
            "generated_at": datetime.now().isoformat(),
            "sites": {},
            "global_people": {},
            "global_events": []
        }
        global_path.parent.mkdir(parents=True, exist_ok=True)
        with open(global_path, 'w', encoding='utf-8') as f:
            json.dump(guard_data, f, ensure_ascii=False, indent=2)
        guard = GlobalGuard(str(global_path))

    site_name = archive.get('site_name', domain)
    if not site_name:
        site_name = domain

    # ========== 域名匹配：精确匹配 → 前缀匹配 → 自动创建 ==========
    sites = guard.data.get('sites', {})
    if site_name not in sites:
        # 尝试前缀匹配（处理后缀不一致问题）
        site_prefix = site_name.split('.')[0] if '.' in site_name else site_name
        matched_key = None
        for key in sites.keys():
            key_prefix = key.split('.')[0] if '.' in key else key
            if key_prefix == site_prefix:
                matched_key = key
                break
        if matched_key:
            print(f"   DOMAIN_MATCH: '{site_name}' -> '{matched_key}' (prefix match)")
            site_name = matched_key
        else:
            print(f"   DOMAIN_MATCH: '{site_name}' not found in global, will auto-create")

    # 检查站点是否已存在
    existing_site = guard.data.get('sites', {}).get(site_name, {})

    # ========== QUANTUM GUARD: Static fields are IMMUTABLE ==========
    PROTECTED_FIELDS = {'config', 'persona', 'keyword_map', 'author_info', 'domain', 'prefix', 'location'}

    for field in PROTECTED_FIELDS:
        if field in existing_site and field in archive:
            if existing_site[field] != archive[field]:
                print(f"   QUANTUM BLOCK: '{field}' in site '{site_name}' is PROTECTED. Keeping old value.")
                archive[field] = copy.deepcopy(existing_site[field])

    # ========== QUANTUM GUARD: Articles append-only via GlobalGuard ==========
    new_articles = archive.get('articles', [])
    added_articles = guard.merge_new_articles(site_name, new_articles)

    # ========== QUANTUM GUARD: Tools append-only via GlobalGuard ==========
    new_tools = archive.get('tools', [])
    added_tools = guard.merge_new_tools(site_name, new_tools)

    # ========== Update global_people (append site_name only, keep existing info) ==========
    author_name = archive.get('author', {}).get('name', '')
    if author_name:
        guard.data.setdefault('global_people', {})
        existing_person = guard.data['global_people'].get(author_name, {})
        sites_list = list(existing_person.get('sites', []))
        if site_name not in sites_list:
            sites_list.append(site_name)
        person_data = copy.deepcopy(existing_person)
        person_data['sites'] = sites_list
        if not person_data.get('location'):
            person_data['location'] = archive.get('author', {}).get('location', '')
        if not person_data.get('occupation'):
            person_data['occupation'] = archive.get('author', {}).get('occupation', '')
        guard.data['global_people'][author_name] = person_data

    # ========== Safe save (auto-backup + validate + atomic write) ==========
    result = guard.save()

    print(f"   OK global_archive.json quantum-sync ({site_name}, "
          f"{result['sites_count']} sites, "
          f"+{added_articles} articles, +{added_tools} tools)")
    print(f"   BACKUP: {result['backup']}")


def _sync_global_archive_legacy(archive, domain):
    """Legacy sync function (fallback when global_guard.py not found)"""
    global_path = Path.home() / '.site_builder' / 'archives' / 'global_archive.json'
    lock_path = global_path.parent / '.global_archive.lock'

    with FileLock(lock_path):
        current_global = load_global_archive()
        site_name = archive.get('site_name', domain)
        if not site_name:
            site_name = domain

        existing_site = current_global.get('sites', {}).get(site_name, {})

        PROTECTED_FIELDS = {'config', 'persona', 'keyword_map', 'author_info', 'domain', 'prefix', 'location'}

        for field in PROTECTED_FIELDS:
            if field in existing_site and field in archive:
                if existing_site[field] != archive[field]:
                    print(f"   QUANTUM BLOCK: '{field}' in site '{site_name}' is PROTECTED. Keeping old value.")
                    archive[field] = copy.deepcopy(existing_site[field])

        existing_articles = existing_site.get('articles', [])
        existing_tools = existing_site.get('tools', [])
        new_articles = archive.get('articles', [])
        new_tools = archive.get('tools', [])

        existing_slugs = {a.get('slug') for a in existing_articles if isinstance(a, dict)}
        merged_articles = list(existing_articles)
        new_article_count = 0

        for a in new_articles:
            if not isinstance(a, dict):
                continue
            slug = a.get('slug')
            if not slug:
                continue
            if slug not in existing_slugs:
                merged_articles.append(a)
                existing_slugs.add(slug)
                new_article_count += 1
            else:
                old_art = next((x for x in existing_articles if x.get('slug') == slug), {})
                if old_art != a:
                    print(f"   QUANTUM BLOCK: Article '{slug}' already exists. Keeping old version.")

        existing_tool_slugs = {t.get('slug') for t in existing_tools if isinstance(t, dict)}
        merged_tools = list(existing_tools)
        new_tool_count = 0

        for t in new_tools:
            if not isinstance(t, dict):
                continue
            slug = t.get('slug')
            if not slug:
                continue
            if slug not in existing_tool_slugs:
                merged_tools.append(t)
                existing_tool_slugs.add(slug)
                new_tool_count += 1
            else:
                old_tool = next((x for x in existing_tools if x.get('slug') == slug), {})
                if old_tool != t:
                    print(f"   QUANTUM BLOCK: Tool '{slug}' already exists. Keeping old version.")

        total_words = sum(a.get('word_count', 0) for a in merged_articles)
        avg_word = round(total_words / max(len(merged_articles), 1), 1)

        site_data = {
            "site_name": site_name,
            "domain": existing_site.get('domain', domain),
            "prefix": existing_site.get('prefix', archive.get('prefix', '')),
            "articles": merged_articles,
            "tools": merged_tools,
            "stats": {
                "total_articles": len(merged_articles),
                "total_tools": len(merged_tools),
                "total_words": total_words,
                "avg_word_count": avg_word
            },
            "updated_at": datetime.now().isoformat()
        }

        for field in ['config', 'persona', 'keyword_map', 'author_info']:
            if field in existing_site:
                site_data[field] = copy.deepcopy(existing_site[field])
            elif field in archive:
                site_data[field] = copy.deepcopy(archive[field])

        author_name = archive.get('author', {}).get('name', '')
        if author_name:
            current_global.setdefault('global_people', {})
            existing_person = current_global['global_people'].get(author_name, {})
            sites_list = list(existing_person.get('sites', []))
            if site_name not in sites_list:
                sites_list.append(site_name)
            person_data = copy.deepcopy(existing_person)
            person_data['sites'] = sites_list
            if not person_data.get('location'):
                person_data['location'] = archive.get('author', {}).get('location', '')
            if not person_data.get('occupation'):
                person_data['occupation'] = archive.get('author', {}).get('occupation', '')
            current_global['global_people'][author_name] = person_data

        current_global['sites'][site_name] = site_data
        current_global['generated_at'] = datetime.now().isoformat()

        try:
            atomic_write_json(global_path, current_global, indent=2, ensure_ascii=False)
            print(f"   OK global_archive.json legacy-sync ({site_name}, {len(merged_articles)} articles [+{new_article_count} new], {len(merged_tools)} tools [+{new_tool_count} new])")
        except Exception as e:
            print(f"   ERROR: global_archive.json write failed: {e}")
            raise


def main():
    try:
        print("[BUILD] build.py V3.13 (Strict Read-Only)")
        print("        Task: scan blog/ -> compare with articles.json -> append new only")
        print("        Date rule: ONLY read from HTML. NEVER generate. Missing -> SKIP.")
        print("        Existing articles: COMPLETELY PROTECTED. build.py never modifies them.")
        print("        Updates: 3 homepages + sitemap.xml/html + llms.txt + robots.txt + archives")
        print()

        domain = detect_domain()
        print(f"[1/7] Domain: {domain}")

        archive = load_site_archive()
        prefix = archive.get('prefix', '')

        print("[2/7] Loading global_archive for merge mode...")
        _global_data = load_global_archive()
        print(f"      Loaded {len(_global_data.get('sites', {}))} sites")

        print(f"[3/7] Scanning blog/ articles...")
        articles = scan_blog_articles()

        if not articles:
            articles = load_articles()
            print(f"      Fallback to existing articles.json ({len(articles)} articles)")

        tools = get_tool_pages(archive)
        compliance = get_compliance_pages()

        print(f"[4/7] Articles: {len(articles)} | Tools: {len(tools)} | Compliance: {len(compliance)}")

        print(f"[5/7] Updating three homepages...")
        update_three_homepages(articles, prefix)

        print(f"[6/7] Generating SEO files...")
        atomic_write_text('sitemap.xml', build_sitemap(domain, articles, tools, compliance))
        print("      OK sitemap.xml")
        atomic_write_text('sitemap.html', build_sitemap_html(domain, articles, tools, compliance))
        print("      OK sitemap.html")
        atomic_write_text('llms.txt', build_llms(domain, articles, tools, archive))
        print("      OK llms.txt")
        atomic_write_text('robots.txt', build_robots(domain))
        print("      OK robots.txt")

        print(f"[7/7] Updating archives...")
        if archive:
            update_site_archive(archive, articles)
            atomic_write_json('site_archive.json', archive, indent=2, ensure_ascii=False)
            print("      OK site_archive.json (merged)")
            print("      Syncing to global_archive.json (with file lock + atomic write)...")
            sync_global_archive(archive, domain)
        else:
            print("      WARN site_archive.json not found, skip")

        print()
        print("[DONE] All files generated successfully!")
        print("       Updated: articles.json, site_archive.json, sitemap.xml, sitemap.html, llms.txt, robots.txt, global_archive.json")
        print("       + three homepages auto-updated (structure preserved)")
        print("       + backups created automatically")

        # 输出备份报告
        print(get_backup_report())

    except Exception as e:
        print(f"\n[ERROR] Execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
