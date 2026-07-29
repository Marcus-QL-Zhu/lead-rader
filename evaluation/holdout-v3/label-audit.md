# Uniform future-job label audit

A separate label agent was denied access to the prediction snapshot and
searched all eight frozen candidates. The observation window was
`[2026-04-01, 2026-07-01)`. Inclusion required an explicit Director,
substantive Director-level functional Head, VP or CxO title, the correct legal
entity, and an exact machine-verifiable publication date inside the window.
Managers, experts, engineers, assistants, relative dates and other group legal
entities were excluded.

## DataMesh

Queries covered the company name plus Director/Head/VP/总监/负责人, LinkedIn and
the official careers page. The official page offered only undated open
applications by function. An Australian fintech company with the same name
was excluded. No qualifying role was observed.

## 矩阵超智

Queries covered the official careers site, BOSS and LinkedIn. The official
site had 投融资经理 and other manager/engineer roles without exact dates. A
BOSS page containing 投融资经理/总监 was updated on 2026-07-02, outside the
window, and did not establish the original publication date. No qualifying
role was observed.

## 云深处科技

Queries covered BOSS, Liepin and general web indexes. Window-dated results
included 解决方案工程师 and SLAM算法工程师 only. A “负责人” occurrence described
the recruiter, not the vacancy. No qualifying role was observed.

## 非夕科技

Queries covered official careers, LinkedIn and BOSS. The official page was an
undated general join page; the identified LinkedIn vacancy was Sales Engineer
and outside the window. A BOSS company page belonged to another legal entity.
No qualifying role was observed.

## 金马游乐

Queries covered the official talent page, BOSS and 51job. The official page
described a career path containing 总监, not an advertised role. A Sales
Manager result was manager-level and dated 2026-07-01, the excluded right
boundary. No qualifying role was observed.

## 模塑科技

Queries covered Zhaopin, 51job, BOSS and the official site. Results were
engineer/supervisor/manager roles or another legal entity; mentions of an IT
head or operations director were people in company news, not vacancies. No
qualifying role was observed.

## 博世力士乐（中国）

The agent inspected the Bosch SmartRecruiters API and legal entity on all
China senior-title hits. Window-dated Department Head, Regional Head, Chief
Technologist, assistant and architect results belonged to other Bosch legal
entities or individual-contributor tracks. Bosch Rexroth entity results were
planner, supervisor, engineer or manager roles. No qualifying role was
observed.

## 西门子（中国）

The official China job list and pagination endpoints were searched for
Director, Head, VP, 总监, 负责人 and Chief. Director, Go-to-Market was dated
2026-07-06; Demand Generation 产品负责人 was dated 2026-07-09; Country
Manager-China was dated 2026-07-01. All were outside the half-open window.
No qualifying role was observed.

The empty label set is retained unchanged. It was not backfilled using
undated, relative-date, lower-level, wrong-entity or boundary-excluded roles.
