<!-- cSpell:disable -->
# راهنمای لایه‌به‌لایه (پیازی) کد شبیه‌ساز MPD

> این راهنما برنامه را مثل یک پیاز باز می‌کند: از بیرونی‌ترین لایه (برنامه در یک جمله) شروع می‌کند و لایه‌به‌لایه به درون می‌رود تا به هسته (معادله‌ها) برسد.
> هر لایه به‌تنهایی کامل است؛ در ارائه می‌توانید در هر لایه‌ای که لازم بود بایستید.
> نظریه، نتایج و تصمیم‌ها در `README_FA.md` آمده‌اند. همه داده‌ها **ساختگی** هستند.

---

## لایه ۱ – برنامه در یک جمله

**برنامه فشار گل را در طول چاه حساب می‌کند (هیدرولیک پایا) و سپس با یک مدل چهارحالته (فشار استندپایپ، فشار پشت چوک، دبی، بازشدگی چوک) نشان می‌دهد که هنگام تغییر دبی پمپ و اتصال لوله، فشار ته‌چاه در زمان چطور تغییر می‌کند و یک PID معکوس‌عمل چطور با حرکت چوک آن را داخل پنجره امن نگه می‌دارد.**

## لایه ۲ – کاربر چه می‌بیند

شش صفحه، به ترتیب گام‌های پایان‌نامه:

| صفحه | کاربر چه کاری انجام می‌دهد | توابع پشت صحنه |
|---|---|---|
| 1 Project & Case | ورودی را وارد یا بارگذاری می‌کند، خطاها و پیشنهادها را می‌بیند، پیشنهاد را با Apply می‌پذیرد | `config_errors`، `suggestions`، `apply_suggestion`، `read_case_csv` |
| 2 Steady Hydraulics | نتایج پایا: BHP، SPP، p_c، ECD، پروفایل، مسیر فشار، رژیم جریان | `solve_steady` |
| 3 Dynamic Model | یک سناریو را با حل‌کننده دلخواه اجرا می‌کند و موازنه جرم و همگرایی را می‌بیند | `simulate`، `check_convergence`، `depth_profiles` |
| 4 Control & Scenarios | سناریوهای S0 تا S4، مقایسه چوک ثابت / P / PI / PID، معیارهای پروپوزال، تنظیم PID | `run_steady_rates`، `run_dynamic`، `run_compare`، `tune_pid` |
| 5 Sensitivity | سناریوی S5 و حساسیت پایا | `run_sensitivity`، `steady_sensitivity`، `sweep` |
| 6 Validation & Evidence | سه سطح اعتبارسنجی با منبع هر عدد | `level1`، `level2`، `level3` |

اصل مهم: **رابط کاربری هیچ فیزیکی ندارد.** `app.py` فقط تابع‌های پوشه `mpd/` را صدا می‌زند و نتیجه را نشان می‌دهد. اجراهای طولانی در یک thread جداگانه انجام می‌شوند (`run_task`) و پیشرفت آن‌ها از طریق یک صف (queue) به پنجره می‌رسد. دکمه Cancel یک پرچم را روشن می‌کند و `simulate` در هر گام نمونه‌برداری آن را بررسی می‌کند.

## لایه ۳ – معماری: فایل‌ها و جریان داده

```
config/base_case.yaml ──► well.load_config ──► migrate_config ──► config_errors ──► build_case ──► Case (SI)
                                                                                     │
                                         make_segments ──► seg (جدول قطعه‌ها) ◄────┘
                                                │
             hydraulics.solve_steady ◄──────────┤──────────► dynamics.simulate ◄── scenarios.yaml
                    │                           │                 │   (choke.py، controller.py)
                SteadyResult               flow_path (V_d, V_a, M) │
                    │                                              ▼
                 plots.py ◄───────── metrics.py ◄────────── SimulationResult
                    │
         app.py (پنجره)  /  main.py (خط فرمان → outputs/)
```

| لایه | فایل‌ها |
|---|---|
| ورودی و هندسه | `units.py`، `well.py`، `library.py` |
| معادلات حوزه | `hydraulics.py`، `choke.py`، `controller.py` |
| حل‌کننده | `dynamics.py` |
| اجرای سناریو و تحلیل | `scenarios.py`، `analysis.py`، `metrics.py`، `validation.py`، `suggestions.py` |
| خروجی | `plots.py`، `app.py`، `main.py` |
| افزونه خارج از دامنه | `extensions.py` (surge/swab) |

## لایه ۴ – هر فایل چه کاری می‌کند

- **`units.py`**: ضریب‌های تبدیل واحد (ft، in، psi، ppg، gpm، cP، lbf/100ft²). همه محاسبات در SI انجام می‌شوند.
- **`well.py`**:
  - `load_config` فایل را می‌خواند و `migrate_config` فایل‌های قدیمی (چوک Cv، مدل یک‌حالته) را به قالب جدید تبدیل می‌کند و فهرست تغییرات را برمی‌گرداند.
  - `config_errors` همه خطاهای فیزیکی را با پیام خوانا فهرست می‌کند: مقادیر غیرمثبت، `p_pore ≥ p_frac`، نبود فضای حلقوی، حدود نادرست چوک، مسیر چاه ناممکن، و هدف خارج از پنجره.
  - `build_case` همه چیز را به SI تبدیل می‌کند و dataclass‌های `Case`، `ChokeSpec`، `ActuatorSpec`، `MeasurementSpec`، `PIDGains` و `SolverSpec` را می‌سازد.
  - `make_segments` چاه را به قطعه‌های حدود ۳۰ متری تقسیم می‌کند و `flow_path` مقادیر V_d، V_a و M را حساب می‌کند.
- **`hydraulics.py`**:
  - `api_critical_velocity`، `api_laminar` و `api_turbulent` روابط پروپوزال هستند.
  - `exact_*` مدل مقایسه‌ای است.
  - `friction_gradient` رژیم جریان را انتخاب می‌کند (با باند گذر ۱۰٪).
  - `pressure_losses` افت‌ها را حساب کرده و در `tanh(q/q_ε)` ضرب می‌کند.
  - `solve_steady` نتیجه پایا را برمی‌گرداند: dataclass `SteadyResult` همراه جدول قطعه‌ها و پروفایل فشار.
- **`choke.py`**: `choke_flow` (معادله روزنه)، `choke_pressure` (معکوس آن)، `opening_for` (یافتن z0 با brentq) و `actuator_rate` (عملگر).
- **`controller.py`**: کلاس `PID` (معکوس‌عمل، فیلتر مشتق، back-calculation) و `controller_gains` که ضرایب P، PI یا PID را برمی‌گرداند.
- **`dynamics.py`**:
  - `FourStateModel.rhs` همان چهار معادله حالت است.
  - `simulate` حلقه زمانی را اجرا می‌کند و نتیجه را در `SimulationResult` برمی‌گرداند.
  - `check_convergence` اجرا را با نصف گام تکرار می‌کند.
  - `steady_state_residual` آزمون مشتق صفر است.
  - `depth_profiles` پروفایل فشار را در چند لحظه حساب می‌کند.
- **`metrics.py`**: تابع `performance_metrics` شاخص‌ها را حساب می‌کند و `check_criteria` معیارها را PASS یا FAIL می‌کند.
- **`scenarios.py`**: اجرای S0 تا S5 از روی `scenarios.yaml`، جدول مقایسه شاخص‌ها و `bpp_check`.
- **`validation.py`**: سه سطح اعتبارسنجی. سطح ۲ و ۳ بدون فایل مرجع «در انتظار» می‌مانند.
- **`analysis.py`**: حساسیت پایا، جاروب پارامتر و `tune_pid` (قاعده تنظیم پروپوزال به‌صورت تکرارپذیر).
- **`suggestions.py`**: پیشنهادها با سه بخش «مشکل / چرا / چطور» و فهرست تغییرات (`changes`)؛ هیچ تغییری بدون Apply کاربر انجام نمی‌شود.
- **`library.py`**: یک فایل CSV شامل همه ورودی‌ها، با یک ستون برای هر حالت. سلول خالی یعنی «مثل حالت اول».
- **`plots.py`**: همه نمودارها. هر تابع روی یک `Figure` رسم می‌کند تا هم در پنجره و هم در PNG استفاده شود.

## لایه ۵ – درون تابع‌های مهم

### `solve_steady`

1. افت‌ها برای دبی q حساب می‌شوند (`pressure_losses`).
2. اگر z داده نشده باشد، `p_c* = p_target − ρgh − ΔP_a` و z0 از `opening_for` پیدا می‌شود. اگر z0 به حد z_min یا z_max برسد، `target_reachable = False` می‌شود و این وضعیت در صفحه ۱ و ۲ نشان داده می‌شود.
3. `BHP = p_c + ρgh + ΔP_a` و `SPP = p_c + ΔP_d + ΔP_bit + ΔP_a`.

### `simulate` (حلقه زمانی)

```
x0 = [SPP, p_c*, q0, z0, 0]                      ← شرایط اولیه از حالت پایا
برای هر k (هر Ts ثانیه):
    BHP_m = BHP(t − θm) (+ نویز اختیاری)          ← اندازه‌گیری تأخیری و نمونه‌برداری‌شده
    z_cmd = PID(e) یا z0 + تغییر زمان‌بندی‌شده    ← کنترل‌گر یا حلقه باز
    z_applied = z_cmd(t − θa)                     ← تأخیر خالص عملگر
    solve_ivp(rhs, [t, t+Ts], Radau)              ← چهار معادله (و متغیر حسابرسی)
    ذخیره خروجی‌ها (BHP، SPP، دبی‌ها، افت‌ها، ECD، جمله‌های PID ...)
پس از حلقه: وضعیت پنجره، باقیمانده موازنه جرم، شاخص‌ها
```

### `PID.update(e)`

```
P = Kp e
D = a D_قبلی + Kd/(T_f + Ts) (e − e_قبلی)        a = T_f/(T_f + Ts)
u = P + I + D ;  z = z0 − u ;  z_sat = محدود به [z_min, z_max]
I_بعدی = I + Ts (Ki e + (u_sat − u)/T_t)          ← back-calculation
```

### `tune_pid`

برای Kp یک جدول نامزد آزموده می‌شود و بزرگ‌ترین مقدار «بدون نوسان مشخص» انتخاب می‌شود. سپس برای Ki کمترین RMSE بدون نوسان انتخاب می‌شود. Kd فقط وقتی اضافه می‌شود که فراجهش بیش از ۵٪ باشد. همه آزمون‌ها در یک جدول گزارش می‌شوند و نتیجه نهایی همراه با دلیل آن نمایش داده می‌شود.

## لایه ۶ – هسته: فیزیک و ریاضی

همه روابط با شماره بخش پروپوزال در `README_FA.md` (بخش‌های ۵ تا ۷) و در docstring هر تابع آمده‌اند. سه نکته عددی مهم:

1. **هموارسازی tanh**: جمله تنش تسلیم در دبی صفر ناپیوسته است. ضرب در `tanh(q/q_ε)` آن را پیوسته می‌کند. q_ε بسیار کوچک است (`10⁻⁴` دبی کاری)، پس دستگاه سخت می‌شود و به همین دلیل Radau لازم است.
2. **باند گذر آرام به آشفته**: بدون این باند، پرش اصطکاک در v_c جریان را دقیقاً روی v_c گیر می‌اندازد و حل‌کننده گام‌های بی‌نهایت کوچک برمی‌دارد. این مشکل در عمل دیده شد: اجرای ۲۰ ثانیه‌ای تمام نمی‌شد، و پس از افزودن باند ۰٫۲ ثانیه طول کشید.
3. **Δp⁺ هموار** در معادله چوک: مشتق‌پذیری نزدیک Δp = 0 را تضمین می‌کند.

## پیوست الف – کلیدهای `base_case.yaml`

| بخش | کلیدها |
|---|---|
| `well` | `survey_file` یا `survey`، `hole_sections`، `drill_string`، `bit.nozzles_32nd`، `bit.discharge_coefficient` (C_dn) |
| `mud` | `density_ppg`، `pv_cp`، `yp_lbf100ft2` |
| `compressibility` | `fluid_bulk_modulus_pa` (β_f)، `wall_bulk_modulus_pa` (β_w) |
| `operation` | `pump_rate_gpm`، `bpp_rate_gpm` |
| `pressure_window` | `pore_ppg`، `fracture_ppg`، `safety_margin_psi` (δ)، `target_ppg` (عدد یا `midpoint`) |
| `choke` | `max_area_in2`، `discharge_coefficient`، `characteristic`، `rangeability`، `min_opening`، `max_opening`، `downstream_pressure_psi`، `max_working_pressure_psi` |
| `actuator` | `time_constant_s`، `max_rate_per_s`، `dead_time_s` |
| `measurement` | `delay_s`، `sample_time_s`، `noise_std_psi`، `noise_seed` |
| `controller` | `kp_per_bar`، `ki_per_bar_s`، `kd_s_per_bar`، `derivative_filter_n`، `tracking_time_s` |
| `solver` | `method`، `rtol`، `atol`، `max_step_s`، `convergence_limit_percent` |
| `numerics` | `segment_length_m`، `friction_model`، `flow_smoothing_fraction`، `regime_transition_width` |
| `criteria` | `max_overshoot_percent`، `max_settling_time_s`، `settling_band_percent`، `min_time_in_window_percent` |

واحد هر کلید در انتهای نام آن آمده است (`_ppg`، `_psi`، `_s`، ...).

## پیوست ب – آزمون‌ها (۷۶ آزمون)

هر آزمون با یک نتیجه **مستقل** مقایسه می‌شود:

- **`test_hydraulics.py`**:
  - v_c و گرادیان‌ها در برابر محاسبه دستی میدانی
  - پیوستگی باند گذر
  - tanh (علامت، صفر و tanh(1))
  - Hagen-Poiseuille، پروفایل Buckingham و حد نیوتنی Hanks
  - رابطه مته
  - ρgh، رابطه SPP، نگه داشتن هدف، تعاریف ECD و U-tube
- **`test_choke_controller.py`**:
  - معادله روزنه دستی، حدهای Δp⁺، معکوس چوک، منحنی هم‌درصد، brentq
  - عملگر (r_max، تأخیر مرتبه اول، توقف در حد)
  - PID: معکوس‌عمل، خطای ماندگار کتاب درسی `1/(1+Kp)`، حذف خطا با PI، تعادل back-calculation، گام اول فیلتر مشتق
- **`test_inputs.py`**:
  - رد ورودی نادرست (۱۱ حالت پارامتری و هندسه ناممکن)
  - وسط پنجره و β_eff
  - V_d، V_a و M با محاسبه دستی
  - تبدیل فایل قدیمی، رفت‌وبرگشت CSV و پارامتر ناشناخته
- **`test_dynamics.py`**:
  - مشتق صفر در حالت پایا، S0 بدون رانش
  - پایان S1 و S2 برابر حالت پایای مستقل
  - حد سرعت عملگر و تأخیر اندازه‌گیری
  - موازنه جرم و همگرایی، توافق RK45 با Radau
  - S3 از پنجره خارج می‌شود و S4 معیارها را برآورده می‌کند
  - بسته شدن چوک هنگام توقف پمپ
  - جهت حساسیت β_eff و تکرارپذیری نویز
- **`test_metrics_validation.py`**:
  - زمان نشست مرتبه اول و فراجهش مرتبه دوم `exp(−ζπ/√(1−ζ²))`
  - RMSE، MAPE، پنجره و معیارها
  - پیشنهادها (ورودی دست‌نخورده می‌ماند و پس از Apply هدف قابل دسترس می‌شود)
  - سطح ۱، ۲ و ۳ اعتبارسنجی، حساسیت و افزونه surge/swab

## پیوست ج – پرسش‌های احتمالی جلسه دفاع

- **چرا Radau؟** دستگاه سخت است: ثابت زمانی دبی در نزدیکی صفر (به خاطر tanh) حدود میلی‌ثانیه است، در حالی که پاسخ فشار ده‌ها ثانیه طول می‌کشد. RK45 هم قابل انتخاب است و همان جواب را می‌دهد (آزمون موجود است).
- **از کجا معلوم است مدل درست حل شده؟** چهار شاهد وجود دارد:
  - مشتق‌ها در نقطه پایا صفرند.
  - پایان S1 و S2 با حل پایای مستقل برابر است.
  - موازنه جرم حدود 1e-15 درصد است.
  - نصف کردن گام نتیجه را کمتر از 1e-4 درصد تغییر می‌دهد.
- **چرا PID با PI یکسان شد؟** قاعده تنظیم پروپوزال Kd را فقط وقتی اضافه می‌کند که فراجهش بیش از ۵٪ باشد، و در اینجا فراجهش ۱٫۱۳٪ است.
- **آیا مدل اعتبارسنجی شده است؟** فقط در سطح ۱ (کد) و با داده ساختگی. سطح ۲ و ۳ منتظر داده منتشرشده هستند و هیچ عددی ساخته نشده است.
- **حساس‌ترین پارامتر کدام است؟** β_eff. سیستم سخت‌تر (β بزرگ‌تر) سریع‌تر به چوک پاسخ می‌دهد و انحراف کمتری دارد.
