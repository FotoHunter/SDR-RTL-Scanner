#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_report.py — генерация HTML-отчётов из существующей базы станций.

Использование:
  python3 gen_report.py --base stations_spb.json --region spb --start 87.5 --stop 108.0
  python3 gen_report.py --base stations_spb.json --region spb --all

Опции:
  --base       путь к JSON-базе станций (обязательный)
  --region     регион (для заголовка и имени файла)
  --start      нижняя граница диапазона (МГц)
  --stop       верхняя граница диапазона (МГц)
  --all        вывести все станции из базы (игнорирует start/stop)
  --output     путь к выходному HTML (если не указан — генерируется автоматически)
"""

import os
import json
import argparse
from datetime import datetime

def export_html(data, region="spb", output_path=None, freq_min=None, freq_max=None):
    # --- ДЕФОЛТНЫЕ ГРАНИЦЫ ДИАПАЗОНА ---
    DEFAULT_MIN = 65.5
    DEFAULT_MAX = 108.5

    # Сохраняем исходные значения для проверки (были ли переданы пользователем)
    user_min = freq_min is not None
    user_max = freq_max is not None


    # Применяем дефолты для расчетов
    calc_min = freq_min if user_min else DEFAULT_MIN
    calc_max = freq_max if user_max else DEFAULT_MAX

    stations = data.get("stations", )
    """
    data: dict с ключом 'stations' (список станций)
    freq_min/freq_max: фильтр по частоте (МГц), None = без фильтра
    """


    # Фильтрация (теперь freq_min/max гарантированно числа)
    if user_min or user_max:
        filtered = []
        for s in stations:
            f = s.get("freq")
            if f is None:
                continue
            if calc_min <= f <= calc_max:
                filtered.append(s)
        stations = filtered

    scan_date = data.get("scan_date", datetime.now().isoformat())
    ppm = data.get("ppm", "?")
    mode = data.get("mode", "report")

    n_total = len(stations)
    n_rds = sum(1 for s in stations if s.get("rds", {}).get("PI"))
    n_ps = sum(1 for s in stations if s.get("rds", {}).get("PS"))
    n_rt = sum(1 for s in stations if s.get("rds", {}).get("RadioText"))
    n_named = sum(1 for s in stations if s.get("name_ru"))
    n_lost = sum(1 for s in stations if s.get("status") == "lost")
    n_new = sum(1 for s in stations if s.get("status") == "new")

    stations.sort(key=lambda s: s.get("freq", 0))

    rows = ""
    import html as html_lib

    for st in stations:
        freq = st.get("freq", 0)
        signal = st.get("signal", 0)
        name_ru = st.get("name_ru", "") or "—"
        rds = st.get("rds", {}) or {}
        pi = rds.get("PI", "—")
        ps = rds.get("PS", "—")
        pty = rds.get("PTY", "—")
        tp = rds.get("TP", "—")
        ta = rds.get("TA", "—")
        rt = rds.get("RadioText", "—")
        stereo = st.get("stereo")
        status = st.get("status", "active")
        last_seen = st.get("last_seen", "")[:10] if st.get("last_seen") else ""

        # Лампочка стерео (используем Unicode, он работает везде)
        if stereo is True:
            ster_str = '<span style="color:#2ecc71;">●</span>' # GUI
            # Для текстовых браузеров цвет не сработает, но символ останется
        elif stereo is False:
            ster_str = '<span style="color:#95a5a6;">○</span>'
        else:
            ster_str = '<span style="color:#7f8c8d;">?</span>'

        row_class = ""
        if status == "lost":
            row_class = ' class="row-lost"'
        elif status == "new":
            row_class = ' class="row-new"'

        # --- ВАЖНО: Используем <font> для совместимости с ELinks ---
        # ELinks игнорирует class="station-name", но видит <font color>
        # Name RU: Оранжевый
        name_cell = f'<td class="name-cell"><font color="#ffcc80"><b>{html_lib.escape(str(name_ru))}</b></font></td>'
        # PI: Красный
        pi_cell = f'<td class="col-rds-meta"><font color="#ff8080"><tt>{html_lib.escape(str(pi))}</tt></font></td>'
        # PTY: Фиолетовый
        pty_cell = f'<td class="col-rds-meta"><font color="#bb88ff">{html_lib.escape(str(pty))}</font></td>'
        # RadioText: Зелёный
        rt_cell = f'<td class="col-rt"><font color="#aaffaa"><i>{html_lib.escape(str(rt))}</i></font></td>'

        rows += (
            f"      <tr{row_class}>\n"
            f"        <td class=\"col-freq\">{freq:.1f}</td>\n"
            f"        <td class=\"col-signal\">{signal:.1f}</td>\n"
            f"{name_cell}\n"                 # <-- Цветное имя (работает везде)
            f"{pi_cell}\n"                    # <-- Цветной PI (работает везде)
            f"        <td class=\"col-rds-meta\">{html_lib.escape(str(ps))}</td>\n"

            f"{pty_cell}\n"                    # <-- Цветной PTY (работает везде)
            f"        <td>{ster_str}</td>\n"
            f"        <td class=\"col-rds-meta\">{html_lib.escape(str(tp))}</td>\n"
            f"        <td class=\"col-rds-meta\">{html_lib.escape(str(ta))}</td>\n"
            f"{rt_cell}\n"                     # <-- Цветный RadioText (работает везде)
            f"        <td>{status}</td>\n"
            f"        <td>{last_seen}</td>\n"
            f"      </tr>\n"
        )

    js_code = """
<script>
let sortDir = {};
function sortTable(col) {
  const table = document.getElementById("stations");
  const tbody = table.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr"));
  const dir = sortDir[col] = !sortDir[col];
  rows.sort((a, b) => {
    let x = a.cells[col].textContent.trim();
    let y = b.cells[col].textContent.trim();
    let xn = parseFloat(x), yn = parseFloat(y);
    if (!isNaN(xn) && !isNaN(yn)) return dir ? xn - yn : yn - xn;
    return dir ? x.localeCompare(y) : y.localeCompare(x);
  });
  rows.forEach(r => tbody.appendChild(r));
}
</script>
"""

    # --- СТРОКА ДИАПАЗОНА ДЛЯ ОТЧЕТА ---
    # Показываем диапазон в шапке отчета, если он отличается от полного дефолтного
    # или если пользователь явно что-то вводил (чтобы было видно, что срез сделан)
    show_range_in_report = (user_min or user_max)
    
    if show_range_in_report:
        range_str = f"| Range: {calc_min:.1f}–{calc_max:.1f} MHz"
    else:
        range_str = ""


    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FM RDS Scanner Report — {region.upper()}</title>

<style>
    /* ТЕМНАЯ ТЕМА ДЛЯ БРАУЗЕРОВ */
    body {{
        font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
        background-color: #1a1a1a;       /* Почти черный фон */
        color: #e0e0e0;                  /* Светло-серый текст */
        padding: 20px;
        margin: 0;
    }}
    h1 {{ color: #ffffff; margin-bottom: 5px; font-size: 1.5em; }}
    p {{ color: #aaaaaa; font-size: 0.9em; margin-bottom: 15px; }}

    table {{
        width: 100%;
        border-collapse: collapse;
        background-color: #2b2b2b;      /* Тёмный фон таблицы */
        box-shadow: 0 2px 5px rgba(0,0,0,0.5);
        font-size: 0.9em;
    }}
    th, td {{
        border: 1px solid #444;          /* Тёмные границы */
        padding: 6px 8px;
        text-align: left;
        white-space: nowrap;
        color: #e0e0e0;                  /* Базовый цвет текста */
    }}
    th {{
        background-color: #3498db;      /* Синяя шапка */
        color: #ffffff;
        position: sticky;
        top: 0;
        cursor: pointer;
        user-select: none;
        border-bottom: 2px solid #2980b9;
    }}

    /* ЦВЕТА ДЛЯ БРАУЗЕРОВ (перекрывают базовые, но работают вместе с <font> для терминалов) */
    .station-name {{ color: #ffcc80; font-weight: bold; }}
    .rds-text {{ color: #aaffaa; font-style: italic; }}
    .pi-code {{ color: #ff8080; font-family: monospace; }}
    .pty-name {{ color: #bb88ff; }}

    tr:nth-child(even) {{ background-color: #333333; }}
    tr:hover {{ background-color: #444444; }}

    .row-lost {{ color: #666666; text-decoration: line-through; }}
    .row-new {{ background-color: #223322; }}
</style>
{js_code}
</head>
<body>
    <h1>FM RDS Scanner Report — {region.upper()}</h1>
    <p style="color:#aaa;">{range_str}</p>
    <p>Scan date: {scan_date} | PPM: {ppm}</p>
    <table id="stations">
        <thead>
            <tr>
            <th onclick="sortTable(0)">Freq (MHz)</th>
            <th onclick="sortTable(1)">Signal (dB)</th>
            <th onclick="sortTable(2)">Name RU</th>
            <th onclick="sortTable(3)">PI</th>
            <th onclick="sortTable(4)">PS</th>
            <th onclick="sortTable(5)">PTY</th>
            <th>Stereo</th>
            <th onclick="sortTable(7)">TP</th>
            <th onclick="sortTable(8)">TA</th>
            <th onclick="sortTable(9)">RadioText</th>
            <th onclick="sortTable(10)">Status</th>
            <th onclick="sortTable(11)">Last Seen</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
        <!-- КОПИРАЙТ -->
        <tfoot>
            <tr>
                <td colspan="12" style="text-align: center; font-size: 0.8em; color: #888; padding-top: 15px; border-top: 1px solid #444;">
                    &copy; 2026 Andrey E. Smirnov | 
                    <a href="https://github.com/FotoHunter/SDR-RTL-Scanner" style="color: #5bc0de; text-decoration: none;">GitHub</a>
                </td>
            </tr>
        </tfoot>
    </table>
</body>
</html>
    """

    if output_path is None:
        base_dir = "data"
        if not os.path.exists(base_dir):
            os.makedirs(base_dir)

        # Если пользователь задал хоть одну границу, включаем обе в имя файла
        # Даже если вторая осталась дефолтной (например, --start 100 -> 100_108)
        if user_min or user_max:
            f_min_str = f"{calc_min:.1f}".replace('.', '_')
            f_max_str = f"{calc_max:.1f}".replace('.', '_')
            output_path = os.path.join(
                base_dir,
                f"Report_SDR_FM_RDS_Base_{region}_{f_min_str}_{f_max_str}.html"
            )
        else:
            # Если ничего не задано — имя без частот
            output_path = os.path.join(base_dir, f"Report_SDR_FM_RDS_Base_{region}.html")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[*] HTML report: {output_path}")
    return output_path

def main():
    parser = argparse.ArgumentParser(description="Генерация HTML-отчётов из базы станций")
    parser.add_argument("--base", default="SDR_FM_RDS_Base_spb.json", help="Путь к JSON-базе станций в папке data/ (по умолчанию: SDR_FM_RDS_Base_<region>.json)")
    parser.add_argument("--region", default="spb", help="Регион (для заголовка и имени файла)")
    parser.add_argument("--start", type=float, help="Нижняя граница диапазона (МГц)")
    parser.add_argument("--stop", type=float, help="Верхняя граница диапазона (МГц)")
    parser.add_argument("--all", action="store_true", help="Вывести все станции из базы (игнорирует start/stop)")
    parser.add_argument("--output", help="Путь к выходному HTML-файлу")

    args = parser.parse_args()
    base_dir = "data"

    # 1. Формируем полный путь к БАЗЕ ДАННЫХ (ВСЕВОЛНОВОЙ)
    # Если пользователь передал полный путь (с /), используем его. Иначе добавляем папку data.
    if os.path.dirname(args.base):
        base_path = args.base
    else:
        # Если передано только имя файла, подставляем регион в имя, если оно дефолтное
        if args.base == "SDR_FM_RDS_Base_spb.json":
            args.base = f"SDR_FM_RDS_Base_{args.region}.json"
        base_path = os.path.join(base_dir, args.base)

    # Проверка существования базы
    if not os.path.exists(base_path):
        print(f"[!] Ошибка: файл базы не найден: {base_path}")
        print(f"   Подсказка: ожидается файл в папке '{base_dir}'")
        return 1

    # Чтение данных
    with open(base_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "stations" not in data:
        print("[!] Ошибка: в базе нет ключа 'stations'")
        return 1

    # 2. Подготовка параметров ФИЛЬТРАЦИИ (диапазон частот)
    freq_min = None
    freq_max = None

    if not args.all:
        if args.start is not None:
            freq_min = args.start
        if args.stop is not None:
            freq_max = args.stop

    # 3. Формирование пути для ВЫХОДНОГО HTML
    # Вот тут мы добавляем диапазон в имя файла отчёта, так как это срез данных
    output_path = args.output
    if output_path is None:
        if not os.path.exists(base_dir):
            os.makedirs(base_dir)

        # Дефолтные границы
        default_min = 65.5
        default_max = 108.5

        # Подставляем дефолты, если аргумент не передан
        f_min = freq_min if freq_min is not None else default_min
        f_max = freq_max if freq_max is not None else default_max

        if freq_min is not None or freq_max is not None:
            # Формат: data/fm_rds_report_spb_65_108.html
            output_path = os.path.join(
                base_dir,
                f"Report_SDR_FM_RDS_Base_{args.region}_{int(f_min)}_{int(f_max)}.html"
            )
        else:
            # Формат: data/fm_rds_report_spb.html
            output_path = os.path.join(base_dir, f"Report_SDR_FM_RDS_Base_{args.region}.html")

    # Вызов функции генерации
    export_html(
        data,
        region=args.region,
        freq_min=freq_min,
        freq_max=freq_max,
        output_path=output_path
    )

    return 0

if __name__ == "__main__":
    exit(main())
