<script>
import appOptions from "./app-options";

export default appOptions;
</script>

<template>
  <div :class="['app-shell', `${theme}-theme`]">
    <aside class="sidebar">
      <div class="brand"><span>AnalystBench</span></div>
      <nav class="nav-list" aria-label="主导航">
        <button :class="['nav-item', { active: activeView === 'dashboard' }]" @click="navigate('dashboard')"><IconLayoutDashboard :size="19" />总览</button>
        <button :class="['nav-item', { active: activeView === 'dataset' }]" @click="navigate('dataset')"><IconDatabase :size="19" />测试集<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item', { active: activeView === 'results' }]" @click="navigate('results')"><IconClipboardData :size="19" />评测结果<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item', { active: activeView === 'optimization' }]" @click="navigate('optimization')"><IconSparkles :size="19" />Skill 自优化<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item muted', { active: activeView === 'settings' }]" @click="navigate('settings')"><IconSettings :size="19" />设置<IconChevronRight class="nav-arrow" :size="16" /></button>
      </nav>
      <div class="sidebar-foot"><span>© 2026 AnalystBench</span><span>v1.0.0</span><small :class="connection">{{ connection === 'connected' ? 'API 已连接' : connection === 'offline' ? 'API 未连接' : '正在检查 API' }}</small></div>
    </aside>

    <main class="content-area">
      <header class="app-header">
        <div class="breadcrumb"><span>{{ activeView === 'dashboard' ? '总览' : activeView === 'dataset' ? '测试集' : activeView === 'results' ? '评测结果' : activeView === 'optimization' ? 'Skill 自优化' : '设置' }}</span></div>
        <div class="header-actions">
          <label class="date-input"><IconTerminal2 :size="15" /><input type="text" value="2025-06-22 ~ 2025-07-22" aria-label="时间范围" /></label>
          <button class="ghost-button"><IconFileExport :size="16" />导出报告</button>
          <button
            class="theme-toggle"
            type="button"
            :aria-label="theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
            :title="theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
            @click="toggleTheme"
          >
            <IconSun v-if="theme === 'dark'" :size="17" />
            <IconMoon v-else :size="17" />
            <span>{{ theme === 'dark' ? '浅色' : '深色' }}</span>
          </button>
          <button class="avatar" aria-label="用户菜单">A</button>
        </div>
      </header>

      <section v-if="activeView === 'dashboard'" class="dashboard-page">
        <div v-if="(!dashboardLoaded || !dashboardStats?.candidates?.length) && !loading" class="empty-state surface" style="min-height:200px"><IconLayoutDashboard :size="30" /><p>暂无评测数据</p><span>使用 CLI evaluate 生成评测结果后刷新即可查看。</span><button class="ghost-button" @click="loadDashboardData"><IconRefresh :size="16" />加载数据</button></div>
        <template v-else-if="dashboardStats?.candidates?.length">
        <div class="test-set-selector">
          <label><IconDatabase :size="16" />测试集</label>
          <select v-model="selectedTestSet">
            <option value="">全部测试集</option>
            <option v-for="ts in dashboardStats?.test_sets ?? []" :key="ts.key" :value="ts.key">{{ ts.name }}</option>
          </select>
          <span class="comparison-filter-label">对比维度</span>
          <div class="comparison-filter" role="group" aria-label="图表对比维度">
            <button type="button" :class="{ active: dashboardComparisonDimension === 'harness' }" :aria-pressed="dashboardComparisonDimension === 'harness'" @click="dashboardComparisonDimension = 'harness'">按 Harness</button>
            <button type="button" :class="{ active: dashboardComparisonDimension === 'model' }" :aria-pressed="dashboardComparisonDimension === 'model'" @click="dashboardComparisonDimension = 'model'">按 Model</button>
          </div>
          <label v-if="dashboardComparisonDimension === 'harness'" class="comparison-value-selector">
            <span>Model</span>
            <select v-model="dashboardModelFilter" aria-label="选择要对比的 Model">
              <option value="Average">Average</option>
              <option v-for="model in dashboardModelOptions" :key="model" :value="model">{{ model }}</option>
            </select>
          </label>
          <label v-else class="comparison-value-selector">
            <span>Harness</span>
            <select v-model="dashboardHarnessFilter" aria-label="选择要对比的 Harness">
              <option value="Average">Average</option>
              <option v-for="harness in dashboardHarnessOptions" :key="harness" :value="harness">{{ harness }}</option>
            </select>
          </label>
          <button class="ghost-button" @click="loadDashboardData()"><IconRefresh :size="16" />刷新</button>
        </div>
        <section class="surface score-panel">
          <div class="panel-title">Overall Score <span>({{ dashboardComparisonContext }} · Higher Is Better)</span><IconInfoCircle :size="15" /></div>
          <div class="score-list">
            <article v-for="card in dashboardScoreCards" :key="card.label" class="score-item" :style="{ '--row-color': card.color }">
              <span>{{ card.label }}</span>
              <strong>{{ card.score.toFixed(1) }}</strong>
              <small>AVG DURATION {{ formatDuration(card.duration_ms) }}</small>
              <i><b :style="{ width: `${card.score}%` }" /></i>
            </article>
          </div>
        </section>

        <div class="chart-grid">
          <section class="surface chart-panel"><div class="panel-heading"><h2>Score by Issue Type <span class="chart-dimension">({{ dashboardComparisonContext }} · Hover for Duration)</span></h2><span v-if="dashboardLoaded" class="api-badge"><IconCircleCheck :size="14" />Live Run Data</span></div><ChartCanvas kind="bar" :theme="theme" :labels="categoryBarLabels" :series="categoryBarSeries" :height="276" /></section>
          <section class="surface chart-panel"><div class="panel-heading"><h2>Score Over Time <span class="chart-dimension">({{ dashboardComparisonContext }} · Hover for Duration)</span></h2></div><ChartCanvas kind="trend" :theme="theme" :labels="dailyScoreLabels" :series="dailyScoreSeries" :height="276" /></section>
        </div>

        <section class="surface matrix-panel">
          <div class="panel-heading"><h2>Score by Issue Type <span>(Average by Category)</span></h2><button class="text-button" @click="navigate('results')">查看全部 <IconChevronRight :size="15" /></button></div>
          <div class="matrix-scroll">
            <table class="score-matrix">
              <colgroup>
                <col class="rank-column" />
                <col class="harness-column" />
                <col class="model-column" />
                <col class="score-column" />
                <col class="runtime-column" />
                <col v-for="cat in activeCategories" :key="`column-${cat.key}`" class="category-column" />
              </colgroup>
              <thead>
                <tr>
                  <th class="rank-cell">#</th>
                  <th class="harness-cell">HARNESS</th>
                  <th class="model-cell">MODEL</th>
                  <th class="score-cell">SCORE</th>
                  <th class="runtime-cell">DURATION</th>
                  <th v-for="cat in activeCategories" :key="cat.key" class="category-cell">{{ cat.name }}<small v-if="cat.case_count > 1">（{{ cat.case_count }} case）</small></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in categoryComparisonRows" :key="row.name">
                  <td class="rank-cell" :aria-label="`第 ${row.rank} 名`">
                    <IconMedal v-if="row.rank <= 3" :class="`rank-medal rank-${row.rank}`" :size="17" />
                    <span v-else>{{ row.rank }}</span>
                  </td>
                  <th class="harness-cell" scope="row">{{ row.harness }}</th>
                  <td class="model-cell">{{ row.model }}</td>
                  <td class="score-cell">{{ row.score.toFixed(1) }}</td>
                  <td class="runtime-cell">{{ formatDuration(row.duration_ms) }}</td>
                  <td v-for="(score, index) in row.categoryScores" :key="`${row.name}-${index}`" class="category-cell">{{ score.toFixed(1) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
        </template>
      </section>

      <section v-else-if="activeView === 'dataset'" class="work-page">
        <div class="work-heading"><div><p class="eyebrow">CASE LIBRARY</p><h1>测试集</h1><p>浏览本地 case.json 文件，按测试集和问题分类组织。</p></div><button class="ghost-button" @click="loadLocalCaseTree"><IconRefresh :size="16" />刷新</button><button class="primary-button" @click="showCreateCaseDialog = true"><IconPlus :size="16" />创建 Case</button></div>
        <div v-if="localCaseTree.length === 0 && !loading" class="empty-state surface" style="min-height:200px"><IconFolder :size="30" /><p>暂无本地 Case</p><span>将 case.json 放入 data/results/{test_set}/{category}/{case_dir}/ 目录后刷新即可查看。</span></div>
        <div v-else class="dataset-layout">
          <section class="surface tree-panel">
            <div class="panel-heading"><h2><IconFolder :size="17" />Case 目录</h2></div>
            <div class="result-tree">
              <div v-for="ts in localCaseTree" :key="ts.key" class="result-tree-group">
                <span class="result-tree-heading">{{ ts.name }}</span>
                <div v-for="cat in ts.children" :key="cat.key" class="result-tree-group">
                  <span class="result-tree-subheading">{{ cat.name }}</span>
                  <div v-for="cs in cat.children" :key="cs.key">
                    <div :class="['result-tree-leaf', { selected: selectedLocalCasePath === `${ts.key}/${cat.key}/${cs.key}` }]" @click="selectLocalCase(ts.key, cat.key, cs.key, cs)">
                      <span class="result-tree-leaf-name">{{ cs.case_data?.case_key || cs.name }}</span>
                      <span class="result-tree-leaf-meta">{{ cs.case_data?.claims_count ?? 0 }} 评分项 · {{ cs.case_data?.result_count ?? 0 }} 次评测</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
          <section class="surface detail-panel">
            <div class="panel-heading"><h2>Case 详情</h2><span>{{ selectedLocalCasePath || '未选择' }}</span><button v-if="selectedLocalCasePath" class="ghost-button" @click="openEvaluateDialog"><IconFlask :size="16" />评分</button></div>
            <template v-if="selectedLocalCaseData">
              <div class="case-detail-content">
                <section class="case-detail-card">
                  <div class="case-detail-section-head">
                    <h3>基础信息</h3>
                  </div>
                  <dl class="case-meta-grid">
                    <div><dt>Case Key</dt><dd>{{ (selectedLocalCaseData.case)?.case_key ?? '—' }}</dd></div>
                    <div><dt>问题分类</dt><dd>{{ ((selectedLocalCaseData.case)?.category)?.name ?? '—' }}</dd></div>
                    <div><dt>测试集</dt><dd>{{ ((selectedLocalCaseData.case)?.test_set)?.name ?? '—' }}</dd></div>
                    <div><dt>评分项数</dt><dd>{{ ((selectedLocalCaseData.eval_spec_draft)?.claims)?.length ?? 0 }}</dd></div>
                  </dl>
                </section>
                <section class="case-detail-card case-logs">
                  <div class="case-detail-section-head">
                  <h3>原始日志</h3>
                  <span :class="selectedCaseLogs?.submission_ready ? 'tag-match' : 'tag-partial'">{{ selectedCaseLogs?.submission_ready ? '可提交测评' : '尚未就绪' }}</span>
                  </div>
                  <div class="case-detail-section-body">
                    <div v-if="selectedCaseLogs?.files?.length" class="compact-list">
                      <div v-for="filename in selectedCaseLogs.files" :key="filename" class="compact-list-row">
                        <code>{{ filename }}</code>
                        <span v-if="selectedCaseLogs.primary_log === filename" class="tag-match">主日志</span>
                        <button v-else class="text-button" @click="setSelectedCasePrimary(filename)">设为主日志</button>
                        <button class="tree-delete" title="删除日志" @click="deleteSelectedCaseLog(filename)"><IconTrash :size="14" /></button>
                      </div>
                    </div>
                    <p v-else class="form-note">尚未上传日志。Case 可以保留，但提交整个测试集测评时会失败。</p>
                    <div class="inline-upload">
                      <label :class="['ghost-button', 'case-log-upload-trigger', { disabled: caseLogsUploading }]">
                        <IconCloudUpload :size="15" />
                        {{ caseLogsUploading ? '上传中…' : '选择并上传日志' }}
                        <input type="file" multiple :disabled="caseLogsUploading" @change="onCaseLogFileChange" />
                      </label>
                      <span class="form-note">选择后自动上传，可同时选择多个文件。</span>
                    </div>
                  </div>
                </section>
                <section class="case-detail-card case-json-card">
                  <div class="case-detail-section-head">
                    <h3>Case JSON</h3>
                    <span class="case-section-meta">只读</span>
                  </div>
                  <div class="code-block case-json-block">{{ JSON.stringify(selectedLocalCaseData, null, 2) }}</div>
                </section>
              </div>
            </template>
            <div v-else class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧 Case 查看详情</p></div>
          </section>
        </div>
      </section>

      <section v-if="activeView === 'results'" class="work-page results-page">
        <div class="work-heading"><div><p class="eyebrow">EVALUATION RESULTS</p><h1>评测结果</h1><p>查看评测结果，或选择测试集和测评方式生成新结果。</p></div><div class="heading-actions"><button class="ghost-button" @click="refreshDirectResults(); loadEvaluationSubmissions(); loadEvaluationSchedules()"><IconRefresh :size="16" />刷新</button><button class="ghost-button" @click="openScheduleDialog()"><IconCircleCheck :size="16" />定时测评</button><button class="primary-button" @click="openSubmitEvaluationDialog"><IconFlask :size="16" />提交测评</button></div></div>
        <section v-if="evaluationSchedules.length" class="surface schedule-panel">
          <div class="panel-heading"><h2><IconCircleCheck :size="17" />定时测评</h2><span>{{ evaluationSchedules.length }} 个计划</span></div>
          <div class="schedule-list">
            <div v-for="schedule in evaluationSchedules" :key="schedule.id" class="schedule-row">
              <div class="schedule-main">
                <strong>{{ schedule.name }}</strong>
                <span>{{ schedule.dataset_key }} · 每天 {{ schedule.local_time }} · {{ schedule.timezone }}</span>
                <small>{{ schedule.case_mode === 'all_ready' ? '全部日志就绪 Case' : `${schedule.case_paths.length} 个固定 Case` }} · {{ schedule.target_ids?.length ? schedule.targets.map((item) => item.display_name || item.key).join('、') : schedule.methods.map((item) => `${item.key} v${item.version}`).join('、') }}</small>
              </div>
              <div class="schedule-state">
                <span :class="schedule.enabled ? 'tag-match' : 'tag-missing'">{{ schedule.enabled ? '已启用' : '已停用' }}</span>
                <small>下次：{{ schedule.enabled ? formatScheduleDateTime(schedule.next_run_at, schedule.timezone) : '—' }}</small>
                <small v-if="schedule.latest_run">最近：<span :class="scheduleStatusClass(schedule.latest_run.status)">{{ schedule.latest_run.status }}</span></small>
              </div>
              <div class="schedule-actions">
                <button class="text-button" @click="runEvaluationScheduleNow(schedule)">立即运行</button>
                <button class="text-button" @click="openEvaluationScheduleRuns(schedule)">历史</button>
                <button class="text-button" @click="openScheduleDialog(schedule)">编辑</button>
                <button class="text-button" @click="toggleEvaluationSchedule(schedule)">{{ schedule.enabled ? '停用' : '启用' }}</button>
                <button class="tree-delete" title="删除计划" @click="deleteEvaluationSchedule(schedule)"><IconTrash :size="14" /></button>
              </div>
            </div>
          </div>
        </section>
        <section v-if="evaluationSubmissions.length" class="surface submission-panel">
          <div class="panel-heading"><h2>测评批次</h2><span>{{ evaluationSubmissions.length }} 个批次</span></div>
          <div class="submission-layout">
            <div class="submission-list">
              <button v-for="submission in evaluationSubmissions" :key="submission.id" :class="['submission-item', { active: selectedSubmissionId === submission.id }]" @click="selectEvaluationSubmission(submission.id)">
                <strong>{{ submission.dataset_key }} · {{ submission.timestamp }}</strong>
                <span :class="submission.status === 'completed' ? 'tag-match' : submission.status === 'failed' || submission.status === 'completed_with_errors' ? 'tag-missing' : 'tag-partial'">{{ submission.status }}</span>
                <small>{{ submission.case_count }} Case · {{ submission.target_ids?.length ? submission.targets.map((item) => item.display_name || item.key).join('、') : submission.methods.map((item) => item.key).join('、') }}<template v-if="submission.schedule_run_id"> · 定时</template></small>
              </button>
            </div>
            <div class="submission-cases">
              <div v-if="selectedSubmission" class="submission-summary">
                <strong>{{ selectedSubmission.dataset_key }}</strong>
                <span>{{ selectedSubmission.summary.completed ?? 0 }}/{{ selectedSubmission.case_count }} 完成</span>
                <button v-if="['completed', 'completed_with_errors', 'failed'].includes(selectedSubmission.status)" class="ghost-button" @click="showEvaluationSubmissionResults(selectedSubmission)">查看正式结果</button>
                <button v-if="selectedSubmission.target_ids?.length && ['completed', 'completed_with_errors', 'failed'].includes(selectedSubmission.status)" class="ghost-button" @click="openTargetComparison(selectedSubmission)">组合对比</button>
                <button v-if="!['completed', 'completed_with_errors', 'failed', 'cancelled'].includes(selectedSubmission.status)" class="ghost-button" @click="cancelEvaluationSubmission(selectedSubmission)">取消批次</button>
                <button v-if="['completed', 'completed_with_errors', 'failed', 'cancelled'].includes(selectedSubmission.status)" class="tree-delete" title="删除测评批次及正式结果" @click="deleteEvaluationSubmission(selectedSubmission)"><IconTrash :size="14" /></button>
              </div>
              <div v-for="caseRun in submissionCaseRuns" :key="caseRun.id" class="submission-case-row">
                <div>
                  <strong>{{ caseRun.case_path }}</strong>
                  <small>打分：{{ caseRun.scoring_status }}</small>
                  <small class="method-run-links">
                    <span v-for="methodRun in caseRun.methods" :key="methodRun.id">{{ methodRun.key }} {{ methodRun.status }} · {{ formatMethodRunTiming(methodRun) }} <button v-if="methodRun.attempt" class="text-button" @click="openMethodArtifacts(methodRun)">审计</button></span>
                  </small>
                </div>
                <span :class="caseRun.status === 'completed' ? 'tag-match' : caseRun.status === 'failed' || caseRun.status === 'completed_with_errors' ? 'tag-missing' : 'tag-partial'">{{ caseRun.status }}</span>
                <button v-if="caseRun.status === 'failed' || caseRun.status === 'completed_with_errors'" class="text-button" @click="retryEvaluationCaseRun(caseRun)">重试失败项</button>
              </div>
            </div>
          </div>
        </section>
        <div class="source-tabs">
          <button :class="['source-tab', { active: resultSource === 'tmp' }]" @click="resultSource = 'tmp'; refreshDirectResults()"><IconFolder :size="16" />临时结果</button>
          <button :class="['source-tab', { active: resultSource === 'formal' }]" @click="resultSource = 'formal'; refreshDirectResults()"><IconClipboardData :size="16" />正式结果</button>
        </div>
        <!-- Tmp results -->
        <template v-if="resultSource === 'tmp'">
          <div v-if="directResultList.length === 0 && !resultLoading" class="empty-state surface" style="min-height:200px"><IconClipboardData :size="30" /><p>暂无临时评测结果</p><span>使用 CLI <code>ab evaluate</code> 生成结果后刷新即可查看。</span></div>
          <div v-else class="results-layout">
            <section class="surface result-list-panel">
              <div class="panel-heading"><h2><IconFolder :size="17" />临时结果</h2><span>{{ directResultList.length }} 条</span></div>
              <div v-if="directResultList.length" class="result-list">
                <div v-for="item in directResultList" :key="item.id" class="result-tree-leaf-row">
                  <div :class="['result-tree-leaf', { selected: selectedResultId === item.id }]" @click="item.status !== 'running' && loadDirectResult(item)">
                    <span class="result-tree-leaf-name">{{ formatResultId(item) }}</span>
                    <span class="result-tree-leaf-meta"><span :class="item.status === 'completed' ? 'tag-match' : item.status === 'running' ? 'tag-partial' : 'tag-missing'">{{ item.status === 'running' ? '评分中' : item.status === 'failed' ? '失败' : item.status }}</span></span>
                  </div>
                  <button v-if="item.status === 'completed'" class="ghost-button" title="归档到正式结果集" @click.stop="openMoveDialog(item, 'promote')"><IconCloudUpload :size="14" />归档</button>
                  <button class="tree-delete" title="删除评测结果" @click.stop="deleteDirectResult(item)"><IconTrash :size="14" /></button>
                </div>
              </div>
              <div v-else class="empty-state"><IconClipboardData :size="22" /><p>暂无结果</p></div>
            </section>
          <section v-if="selectedResultData" class="surface result-detail-panel">
            <template v-if="String(selectedResultData.status ?? '') === 'running'">
              <div class="panel-heading"><h2>评分进行中</h2><span>{{ String(selectedResultData.case_key ?? '') }}</span></div>
              <div class="empty-state"><IconFlask :size="30" /><p>语义评分正在执行，请稍候…</p><span>{{ String(selectedResultData.case_key ?? '') }}</span></div>
            </template>
            <template v-else-if="String(selectedResultData.status ?? '') === 'failed'">
              <div class="panel-heading"><h2>评分失败</h2><span>{{ String(selectedResultData.case_key ?? '') }}</span></div>
              <p class="engine-note tone-warning">{{ String((selectedResultData.error)?.message ?? '未知错误') }}</p>
            </template>
            <template v-else-if="parseSummary(selectedResultData)">
              <div class="panel-heading"><h2>评测结果详情</h2><span>{{ String(selectedResultData.case_key ?? '') }} · {{ String(selectedResultData.id ?? '') }}</span></div>
              <p class="engine-note">{{ parseSummary(selectedResultData).engine_note }}</p>
              <section class="surface result-score-panel">
                <div class="panel-title">Overall Score <span>(Higher Is Better)</span></div>
                <div class="result-score-list">
                  <div v-for="(report, idx) in resultRankedReports" :key="'sc-' + report.candidate_name" class="result-score-row" :style="{ '--row-color': resultColors[idx] }">
                    <span class="result-score-name"><i></i>{{ report.candidate_name }}</span>
                    <strong>{{ parseFloat(report.score).toFixed(1) }}</strong>
                    <span class="result-score-duration">生成耗时 {{ formatDuration(resultGenerationDuration(report.candidate_name)) }}</span>
                    <i class="result-score-bar"><b :style="{ width: `${parseFloat(report.score)}%` }" /></i>
                    <span :class="report.passed ? 'tag-match' : 'tag-missing'">{{ report.passed ? '通过' : '未通过' }}</span>
                  </div>
                </div>
              </section>
              <section class="surface result-overview-section">
                <h3>总览</h3>
                <table class="result-overview-table">
                  <thead><tr><th>排名</th><th>报告</th><th>得分</th><th>生成耗时</th><th>结果</th><th>命中</th><th>缺失链</th></tr></thead>
                  <tbody>
                    <tr v-for="(report, idx) in parseSummary(selectedResultData).reports" :key="report.candidate_name">
                      <td>{{ idx + 1 }}</td>
                      <td :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</td>
                      <td>{{ parseFloat(report.score).toFixed(1) }}</td>
                      <td>{{ formatDuration(resultGenerationDuration(report.candidate_name)) }}</td>
                      <td><span :class="report.passed ? 'tag-match' : 'tag-missing'">{{ report.passed ? '通过' : '未通过' }}</span></td>
                      <td>{{ report.hit_count }}/{{ report.claim_count }}</td>
                      <td>{{ report.missing_chains.length ? report.missing_chains.join('；') : '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </section>
              <section v-for="report in parseSummary(selectedResultData).reports" :key="report.candidate_name" class="surface claim-detail-section">
                <div class="panel-heading"><h3 :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</h3><span>得分 {{ parseFloat(report.score).toFixed(1) }} / 100</span></div>
                <table class="claim-detail-table">
                  <thead><tr><th>评分项</th><th>类型</th><th>判定</th><th>得分</th><th>关键字匹配</th><th>结论语义</th></tr></thead>
                  <tbody>
                    <tr v-for="claim in report.claims" :key="claim.id">
                      <td class="claim-statement">{{ claim.statement }}</td>
                      <td class="claim-type">{{ claim.type === 'root_cause' ? '根因' : claim.type === 'classification' ? '分类' : '分析链' }}</td>
                      <td><span :class="relationTagClass(claim.overall_relation)">{{ claim.relation_label }}</span></td>
                      <td class="claim-score">{{ parseFloat(claim.score).toFixed(1) }}</td>
                      <td>
                        <template v-if="claim.keyword_match !== null">
                          <span :class="claim.keyword_match ? 'tag-match' : 'tag-missing'">{{ claim.keyword_match ? '命中' : '未命中' }}</span>
                          <small v-if="claim.evidence_keyword">（{{ claim.keyword_score }}）</small>
                        </template>
                        <template v-else>—</template>
                      </td>
                      <td>
                        <template v-if="claim.conclusion_similarity !== null">
                          {{ (claim.conclusion_similarity * 100).toFixed(0) }}%
                          <small v-if="claim.conclusion_score">（{{ claim.conclusion_score }}）</small>
                        </template>
                        <template v-else>—</template>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="report.claims.some((c) => c.closest_keyword_line)" class="closest-lines">
                  <strong>最接近行</strong>
                  <div v-for="claim in report.claims.filter((c) => c.closest_keyword_line)" :key="'cl-' + claim.id" class="closest-line-item">
                    <span>{{ claim.id }}（第 {{ claim.closest_keyword_line.line_number }} 行）：</span>
                    <code>{{ claim.closest_keyword_line.quote }}</code>
                    <small>相似度 {{ (claim.closest_keyword_line.diagnostic_similarity * 100).toFixed(1) }}%</small>
                  </div>
                </div>
              </section>
              <section v-if="parseSummary(selectedResultData).comparisons.length" class="surface comparison-section">
                <div class="panel-heading"><h3>报告对比</h3></div>
                <table class="comparison-table">
                  <thead><tr><th>基线</th><th>候选</th><th>差值</th><th>判定</th></tr></thead>
                  <tbody>
                    <tr v-for="comp in parseSummary(selectedResultData).comparisons" :key="`${comp.baseline}-${comp.candidate}`">
                      <td>{{ comp.baseline }}</td>
                      <td>{{ comp.candidate }}</td>
                      <td>{{ parseFloat(comp.delta).toFixed(1) }}</td>
                      <td><span :class="comp.classification === 'improved' ? 'tag-match' : comp.classification === 'degraded' ? 'tag-missing' : 'tag-partial'">{{ comp.classification_label }}</span></td>
                    </tr>
                  </tbody>
                </table>
              </section>
              <section class="surface metrics-section">
                <div class="panel-heading"><h3>指标概要</h3></div>
                <table class="metrics-table">
                  <thead><tr><th>报告</th><th>覆盖率</th><th>根因精确</th><th>矛盾数</th><th>禁止命中</th><th>缺失链数</th></tr></thead>
                  <tbody>
                    <tr v-for="report in parseSummary(selectedResultData).reports" :key="'m-' + report.candidate_name">
                      <td :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</td>
                      <td>{{ (Number(report.metrics.claim_coverage) * 100).toFixed(1) }}%</td>
                      <td><span :class="Boolean(report.metrics.root_cause_exact) ? 'tag-match' : 'tag-missing'">{{ Boolean(report.metrics.root_cause_exact) ? '是' : '否' }}</span></td>
                      <td>{{ report.metrics.contradiction_count }}</td>
                      <td>{{ report.metrics.forbidden_hit_count }}</td>
                      <td>{{ report.metrics.missing_chain_count }}</td>
                    </tr>
                  </tbody>
                </table>
              </section>
            </template>
            <div v-else class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧结果查看详情</p></div>
          </section>
          <section v-else class="surface result-detail-panel">
            <div class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧结果查看详情</p></div>
          </section>
        </div>
        </template>
        <!-- Formal results -->
        <template v-if="resultSource === 'formal'">
          <div v-if="directResultList.length === 0 && !resultLoading" class="empty-state surface" style="min-height:200px"><IconClipboardData :size="30" /><p>暂无正式评测结果</p><span>提交测评完成后会自动写入此处；临时结果也可手动归档。</span></div>
          <div v-else class="results-layout">
            <section class="surface result-list-panel">
              <div class="panel-heading"><h2><IconClipboardData :size="17" />正式结果</h2><span>{{ directResultList.length }} 条</span></div>
              <div v-if="Object.keys(formalTree).length" class="result-tree">
                <div v-for="(catTree, tsKey) in formalTree" :key="tsKey" class="result-tree-group">
                  <span class="result-tree-heading">{{ tsKey }}</span>
                  <div v-for="(caseTree, catKey) in catTree" :key="catKey" class="result-tree-group">
                    <span class="result-tree-subheading">{{ catKey }}</span>
                    <div v-for="(items, cdKey) in caseTree" :key="cdKey">
                      <span class="result-tree-leaf-label">{{ cdKey }}</span>
                      <div v-for="item in items" :key="item.id" :class="['result-tree-leaf-row', { 'result-hidden': item.included_in_statistics === false }]">
                        <div :class="['result-tree-leaf', { selected: selectedResultId === item.id }]" @click="loadDirectResult(item)">
                          <span class="result-tree-leaf-name">{{ item.timestamp }}</span>
                          <span class="result-tree-leaf-meta">
                            <span v-if="item.included_in_statistics === false" class="tag-partial">已隐藏</span>
                            <span :class="item.status === 'completed' ? 'tag-match' : 'tag-missing'">{{ item.status }}</span>
                          </span>
                        </div>
                        <button
                          :class="['tree-visibility', { 'is-hide-action': item.included_in_statistics !== false }]"
                          :title="item.included_in_statistics === false ? '显示并计入统计' : '不显示且不计入统计'"
                          :aria-label="item.included_in_statistics === false ? '显示并计入统计' : '不显示且不计入统计'"
                          @click.stop="toggleDirectResultVisibility(item)"
                        ><IconView :size="15" /></button>
                        <button class="tree-move" title="Move" @click.stop="openMoveDialog(item, 'move')"><IconChevronRight :size="14" /></button>
                        <button class="tree-delete" title="删除评测结果" @click.stop="deleteDirectResult(item)"><IconTrash :size="14" /></button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="empty-state"><IconClipboardData :size="22" /><p>暂无结果</p></div>
            </section>
            <section v-if="selectedResultData" class="surface result-detail-panel">
              <template v-if="parseSummary(selectedResultData)">
                <div class="panel-heading"><h2>评测结果详情</h2><span>{{ String(selectedResultData.case_key ?? '') }} · {{ String(selectedResultData.id ?? '') }}</span></div>
                <p class="engine-note">{{ parseSummary(selectedResultData).engine_note }}</p>
                <section class="surface result-score-panel">
                  <div class="panel-title">Overall Score <span>(Higher Is Better)</span></div>
                  <div class="result-score-list">
                    <div v-for="(report, idx) in resultRankedReports" :key="'sc-' + report.candidate_name" class="result-score-row" :style="{ '--row-color': resultColors[idx] }">
                      <span class="result-score-name"><i></i>{{ report.candidate_name }}</span>
                      <strong>{{ parseFloat(report.score).toFixed(1) }}</strong>
                      <span class="result-score-duration">生成耗时 {{ formatDuration(resultGenerationDuration(report.candidate_name)) }}</span>
                      <i class="result-score-bar"><b :style="{ width: `${parseFloat(report.score)}%` }" /></i>
                      <span :class="report.passed ? 'tag-match' : 'tag-missing'">{{ report.passed ? '通过' : '未通过' }}</span>
                    </div>
                  </div>
                </section>
                <section class="surface result-overview-section">
                  <h3>总览</h3>
                  <table class="result-overview-table">
                    <thead><tr><th>排名</th><th>报告</th><th>得分</th><th>生成耗时</th><th>结果</th><th>命中</th><th>缺失链</th></tr></thead>
                    <tbody>
                      <tr v-for="(report, idx) in parseSummary(selectedResultData).reports" :key="report.candidate_name">
                        <td>{{ idx + 1 }}</td>
                        <td :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</td>
                        <td>{{ parseFloat(report.score).toFixed(1) }}</td>
                        <td>{{ formatDuration(resultGenerationDuration(report.candidate_name)) }}</td>
                        <td><span :class="report.passed ? 'tag-match' : 'tag-missing'">{{ report.passed ? '通过' : '未通过' }}</span></td>
                        <td>{{ report.hit_count }}/{{ report.claim_count }}</td>
                        <td>{{ report.missing_chains.length ? report.missing_chains.join('；') : '—' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </section>
                <section v-for="report in parseSummary(selectedResultData).reports" :key="'f-' + report.candidate_name" class="surface claim-detail-section">
                  <div class="panel-heading"><h3 :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</h3><span>得分 {{ parseFloat(report.score).toFixed(1) }} / 100</span></div>
                  <table class="claim-detail-table">
                    <thead><tr><th>评分项</th><th>类型</th><th>判定</th><th>得分</th><th>关键字匹配</th><th>结论语义</th></tr></thead>
                    <tbody>
                      <tr v-for="claim in report.claims" :key="'f-' + claim.id">
                        <td class="claim-statement">{{ claim.statement }}</td>
                        <td class="claim-type">{{ claim.type === 'root_cause' ? '根因' : claim.type === 'classification' ? '分类' : '分析链' }}</td>
                        <td><span :class="relationTagClass(claim.overall_relation)">{{ claim.relation_label }}</span></td>
                        <td class="claim-score">{{ parseFloat(claim.score).toFixed(1) }}</td>
                        <td>
                          <template v-if="claim.keyword_match !== null">
                            <span :class="claim.keyword_match ? 'tag-match' : 'tag-missing'">{{ claim.keyword_match ? '命中' : '未命中' }}</span>
                            <small v-if="claim.evidence_keyword">（{{ claim.keyword_score }}）</small>
                          </template>
                          <template v-else>—</template>
                        </td>
                        <td>
                          <template v-if="claim.conclusion_similarity !== null">
                            {{ (claim.conclusion_similarity * 100).toFixed(0) }}%
                            <small v-if="claim.conclusion_score">（{{ claim.conclusion_score }}）</small>
                          </template>
                          <template v-else>—</template>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <div v-if="report.claims.some((c) => c.closest_keyword_line)" class="closest-lines">
                    <strong>最接近行</strong>
                    <div v-for="claim in report.claims.filter((c) => c.closest_keyword_line)" :key="'fcl-' + claim.id" class="closest-line-item">
                      <span>{{ claim.id }}（第 {{ claim.closest_keyword_line.line_number }} 行）：</span>
                      <code>{{ claim.closest_keyword_line.quote }}</code>
                      <small>相似度 {{ (claim.closest_keyword_line.diagnostic_similarity * 100).toFixed(1) }}%</small>
                    </div>
                  </div>
                </section>
                <section v-if="parseSummary(selectedResultData).comparisons.length" class="surface comparison-section">
                  <div class="panel-heading"><h3>报告对比</h3></div>
                  <table class="comparison-table">
                    <thead><tr><th>基线</th><th>候选</th><th>差值</th><th>判定</th></tr></thead>
                    <tbody>
                      <tr v-for="comp in parseSummary(selectedResultData).comparisons" :key="`${comp.baseline}-${comp.candidate}`">
                        <td>{{ comp.baseline }}</td>
                        <td>{{ comp.candidate }}</td>
                        <td>{{ parseFloat(comp.delta).toFixed(1) }}</td>
                        <td><span :class="comp.classification === 'improved' ? 'tag-match' : comp.classification === 'degraded' ? 'tag-missing' : 'tag-partial'">{{ comp.classification_label }}</span></td>
                      </tr>
                    </tbody>
                  </table>
                </section>
                <section class="surface metrics-section">
                  <div class="panel-heading"><h3>指标概要</h3></div>
                  <table class="metrics-table">
                    <thead><tr><th>报告</th><th>覆盖率</th><th>根因精确</th><th>矛盾数</th><th>禁止命中</th><th>缺失链数</th></tr></thead>
                    <tbody>
                      <tr v-for="report in parseSummary(selectedResultData).reports" :key="'fm-' + report.candidate_name">
                        <td :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</td>
                        <td>{{ (Number(report.metrics.claim_coverage) * 100).toFixed(1) }}%</td>
                        <td><span :class="Boolean(report.metrics.root_cause_exact) ? 'tag-match' : 'tag-missing'">{{ Boolean(report.metrics.root_cause_exact) ? '是' : '否' }}</span></td>
                        <td>{{ report.metrics.contradiction_count }}</td>
                        <td>{{ report.metrics.forbidden_hit_count }}</td>
                        <td>{{ report.metrics.missing_chain_count }}</td>
                      </tr>
                    </tbody>
                  </table>
                </section>
              </template>
              <div v-else class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧结果查看详情</p></div>
            </section>
            <section v-else class="surface result-detail-panel">
              <div class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧结果查看详情</p></div>
            </section>
          </div>
        </template>
      </section>

      <section v-if="activeView === 'optimization'" class="work-page optimization-page">
        <div class="work-heading">
          <div>
            <p class="eyebrow">AUTONOMOUS SKILL IMPROVEMENT</p>
            <h1>Skill 自优化</h1>
            <p>用同一组 Benchmark 比较基线与候选；只有通过 Gate 的版本才会成为 Active。</p>
          </div>
          <div class="heading-actions">
            <button class="ghost-button" @click="loadOptimizationWorkspace"><IconRefresh :size="16" />刷新</button>
            <button class="primary-button" @click="openOptimizationDialog"><IconPlus :size="16" />新建优化实验</button>
          </div>
        </div>

        <div class="optimization-overview">
          <article class="surface optimization-stat"><span>实验总数</span><strong>{{ optimizationSummary.total }}</strong><small>不可变 Skill 版本</small></article>
          <article class="surface optimization-stat"><span>运行中</span><strong>{{ optimizationSummary.running }}</strong><small>自动轮询进度</small></article>
          <article class="surface optimization-stat"><span>已完成</span><strong>{{ optimizationSummary.completed }}</strong><small>含 Early Stop</small></article>
          <article class="surface optimization-stat"><span>当前实验晋升</span><strong>{{ optimizationSummary.promoted }}</strong><small>Gate 通过后原子切换</small></article>
        </div>

        <div v-if="!optimizationExperiments.length && !optimizationLoading" class="empty-state surface optimization-empty">
          <IconSparkles :size="32" />
          <p>还没有 Skill 自优化实验</p>
          <span>选择本地 Skill、claude Target 和 Benchmark Cases，系统会自动生成两个候选并筛选。</span>
          <button class="primary-button" @click="openOptimizationDialog"><IconPlus :size="16" />创建第一个实验</button>
        </div>

        <div v-else class="optimization-layout">
          <aside class="surface optimization-list-panel">
            <div class="panel-heading"><h2>实验</h2><span>{{ optimizationExperiments.length }}</span></div>
            <button
              v-for="experiment in optimizationExperiments"
              :key="experiment.id"
              :class="['optimization-list-item', { active: selectedOptimizationExperimentId === experiment.id }]"
              @click="selectOptimizationExperiment(experiment.id)"
            >
              <span class="optimization-list-main"><strong>{{ experiment.name }}</strong><small>Epoch {{ experiment.current_epoch_number }} / {{ experiment.max_epochs }}</small></span>
              <span :class="optimizationStatusClass(experiment.status)">{{ optimizationStatusLabel(experiment.status) }}</span>
            </button>
          </aside>

          <section class="optimization-detail">
            <template v-if="selectedOptimizationDetail">
              <section class="surface optimization-hero">
                <div>
                  <p class="eyebrow">EXPERIMENT</p>
                  <h2>{{ selectedOptimizationDetail.experiment.name }}</h2>
                  <div class="optimization-meta">
                    <span :class="optimizationStatusClass(selectedOptimizationDetail.experiment.status)">{{ optimizationStatusLabel(selectedOptimizationDetail.experiment.status) }}</span>
                    <code>{{ selectedOptimizationDetail.experiment.id }}</code>
                    <span v-if="selectedOptimizationDetail.experiment.stop_reason">停止原因：{{ selectedOptimizationDetail.experiment.stop_reason }}</span>
                  </div>
                </div>
                <div class="heading-actions">
                  <button v-if="selectedOptimizationDetail.experiment.status === 'created'" class="primary-button" @click="startOptimizationExperiment(selectedOptimizationDetail.experiment)">启动</button>
                  <button v-if="selectedOptimizationDetail.experiment.status === 'failed'" class="primary-button" @click="resumeOptimizationExperiment(selectedOptimizationDetail.experiment)">从断点恢复</button>
                  <button v-if="selectedOptimizationDetail.experiment.status === 'running'" class="ghost-button danger-button" @click="cancelOptimizationExperiment(selectedOptimizationDetail.experiment)">取消</button>
                </div>
              </section>

              <section v-for="epoch in selectedOptimizationDetail.epochs" :key="epoch.id" class="surface optimization-epoch">
                <div class="optimization-epoch-head">
                  <div><span class="epoch-number">E{{ epoch.number }}</span><div><h3>Epoch {{ epoch.number }}</h3><small>Parent {{ epoch.parent_skill_version_id }}</small></div></div>
                  <div><span :class="optimizationStatusClass(epoch.status)">{{ optimizationStatusLabel(epoch.status) }}</span><small v-if="epoch.decision">决策：{{ epoch.decision }}</small></div>
                </div>

                <div class="optimization-flow" aria-label="优化阶段">
                  <span :class="{ done: epoch.evidence?.case_count }">Evidence</span>
                  <i></i>
                  <span :class="{ done: epoch.candidates.length }">2 Candidates</span>
                  <i></i>
                  <span :class="{ done: epoch.candidates.some((item) => item.status === 'screening_selected' || item.status === 'accepted') }">Screening</span>
                  <i></i>
                  <span :class="{ done: epoch.decision }">Gate</span>
                </div>

                <div v-if="epoch.evidence?.case_count" class="optimization-evidence">
                  <div class="panel-heading"><h3>Failure Family / Dimension Evidence</h3><span>{{ epoch.evidence.case_count }} Cases</span></div>
                  <div class="evidence-grid">
                    <table>
                      <thead><tr><th>FAILURE FAMILY</th><th>MEDIAN</th><th>SAMPLES</th></tr></thead>
                      <tbody><tr v-for="row in optimizationObjectRows(epoch.evidence.failure_families)" :key="row.key"><td>{{ row.key }}</td><td>{{ row.value.median_score ?? '—' }}</td><td>{{ row.value.sample_count }}</td></tr></tbody>
                    </table>
                    <table>
                      <thead><tr><th>DIMENSION</th><th>MEDIAN</th><th>SAMPLES</th></tr></thead>
                      <tbody><tr v-for="row in optimizationObjectRows(epoch.evidence.dimensions)" :key="row.key"><td>{{ row.key }}</td><td>{{ row.value.median_score ?? '—' }}</td><td>{{ row.value.sample_count }}</td></tr></tbody>
                    </table>
                  </div>
                </div>

                <div class="candidate-grid">
                  <article v-for="(candidate, index) in epoch.candidates" :key="candidate.id" class="candidate-card">
                    <div class="candidate-head">
                      <span class="candidate-index">C{{ index + 1 }}</span>
                      <div><strong>候选 {{ index + 1 }}</strong><small>{{ candidate.candidate_type }}</small></div>
                      <span :class="optimizationStatusClass(candidate.status)">{{ optimizationStatusLabel(candidate.status) }}</span>
                    </div>
                    <p>{{ candidate.rationale || 'Optimizer 未提供说明。' }}</p>
                    <div v-if="candidateComparison(candidate, 'screening')" class="candidate-metrics">
                      <span>SCREENING DELTA <strong>{{ signedDelta(candidateComparison(candidate, 'screening').metrics.overall_delta) }}</strong></span>
                      <span>FAMILY <strong>{{ optimizationObjectRows(candidateComparison(candidate, 'screening').metrics.family_deltas).length }}</strong></span>
                    </div>
                    <div v-if="candidateComparison(candidate, 'paired_repeated_validation')" class="candidate-metrics">
                      <span>FULL DELTA <strong>{{ signedDelta(candidateComparison(candidate, 'paired_repeated_validation').metrics.overall_delta) }}</strong></span>
                      <span>REPEATS <strong>{{ candidateComparison(candidate, 'paired_repeated_validation').metrics.repeat_count }}</strong></span>
                    </div>
                    <span v-if="candidate.rejection_code" class="candidate-rejection">{{ candidate.rejection_code }}</span>
                    <button class="ghost-button" @click="openOptimizationCandidate(candidate)">查看 Patch 与 Diff</button>
                  </article>
                </div>

                <div v-for="candidate in epoch.candidates" :key="`cmp-${candidate.id}`">
                  <section v-if="candidateComparison(candidate, 'paired_repeated_validation')" class="comparison-breakdown">
                    <div class="panel-heading"><h3>候选比较</h3><span :class="optimizationStatusClass(candidateComparison(candidate, 'paired_repeated_validation').gate.verdict)">{{ candidateComparison(candidate, 'paired_repeated_validation').gate.verdict }}</span></div>
                    <div class="evidence-grid">
                      <table>
                        <thead><tr><th>CASE / FAMILY</th><th>BASELINE</th><th>CANDIDATE</th><th>DELTA</th></tr></thead>
                        <tbody><tr v-for="pair in candidateComparison(candidate, 'paired_repeated_validation').metrics.pairs" :key="pair.case_path"><td>{{ pair.case_family }}<small>{{ pair.case_path }}</small></td><td>{{ pair.baseline_score }}</td><td>{{ pair.candidate_score }}</td><td :class="{ 'delta-positive': pair.delta > 0, 'delta-negative': pair.delta < 0 }">{{ signedDelta(pair.delta) }}</td></tr></tbody>
                      </table>
                      <table>
                        <thead><tr><th>DIMENSION</th><th>DELTA</th></tr></thead>
                        <tbody><tr v-for="row in optimizationObjectRows(candidateComparison(candidate, 'paired_repeated_validation').metrics.dimension_deltas)" :key="row.key"><td>{{ row.key }}</td><td :class="{ 'delta-positive': row.value > 0, 'delta-negative': row.value < 0 }">{{ signedDelta(row.value) }}</td></tr></tbody>
                      </table>
                    </div>
                  </section>
                </div>
              </section>

              <section v-if="optimizationCandidateDetail" class="surface optimization-diff">
                <div class="panel-heading"><h2>候选 Patch 与 Diff</h2><button class="text-button" @click="optimizationCandidateDetail = null">关闭</button></div>
                <p>{{ optimizationCandidateDetail.rationale }}</p>
                <pre>{{ optimizationCandidateDetail.diff || '没有文本差异。' }}</pre>
              </section>
            </template>
            <div v-else class="empty-state surface"><IconInfoCircle :size="30" /><p>选择左侧实验查看详情</p></div>
          </section>
        </div>

        <div v-if="showOptimizationDialog" class="dialog-overlay" @click.self="showOptimizationDialog = false">
          <section class="dialog-card dialog-card-wide surface optimization-dialog">
            <div class="panel-heading"><h2>新建 Skill 自优化实验</h2><span>步骤 {{ optimizationDialogStep }} / 3</span></div>
            <div class="dialog-stepper"><span :class="{ active: optimizationDialogStep >= 1 }">1 Skill</span><i></i><span :class="{ active: optimizationDialogStep >= 2 }">2 Benchmark</span><i></i><span :class="{ active: optimizationDialogStep >= 3 }">3 Gate</span></div>

            <template v-if="optimizationDialogStep === 1">
              <label>实验名称<input v-model.trim="optimizationForm.name" /></label>
              <label>Skill 来源<select v-model="optimizationForm.skill_mode"><option value="new">注册本地 Skill</option><option value="existing">使用已有 Skill</option></select></label>
              <template v-if="optimizationForm.skill_mode === 'new'">
                <div class="form-two-columns"><label>Skill Key<input v-model.trim="optimizationForm.skill_key" placeholder="my-skill" /></label><label>调用名称<input v-model.trim="optimizationForm.invoke_as" placeholder="/my-skill" /></label></div>
                <label>Harness Key<input v-model.trim="optimizationForm.harness_key" placeholder="claude-skill" /></label>
                <label>本地 Skill 目录<input v-model.trim="optimizationForm.source_path" placeholder="/project/.claude/skills/my-skill" /></label>
                <label>显示名称<input v-model.trim="optimizationForm.skill_name" placeholder="默认使用 Skill Key" /></label>
              </template>
              <label v-else>已有 Skill<select v-model="optimizationForm.skill_id"><option value="">请选择</option><option v-for="skill in skills" :key="skill.id" :value="skill.id">{{ skill.name }} · {{ skill.invoke_as }}</option></select></label>
            </template>

            <template v-else-if="optimizationDialogStep === 2">
              <label>claude Evaluation Target<select v-model="optimizationForm.evaluation_target_id" @change="syncOptimizationHarnessKey"><option value="">请选择已冻结 Target</option><option v-for="target in frozenOptimizationTargets" :key="target.id" :value="target.id">{{ target.display_name || target.key }} · {{ target.command_template }}</option></select></label>
              <p class="form-note">Target 命令必须明确包含 {{ optimizationForm.invoke_as || '/skill-name' }}；系统会把冻结版本复制到每个独立工作区。</p>
              <div class="optimization-case-picker">
                <label v-for="item in optimizationCaseOptions" :key="item.path" :class="{ disabled: !item.ready }">
                  <input type="checkbox" :checked="optimizationForm.case_paths.includes(item.path)" :disabled="!item.ready" @change="toggleOptimizationCase(item.path)" />
                  <span>{{ item.label }}</span><small>{{ item.ready ? '日志已就绪' : '缺少日志' }}</small>
                </label>
              </div>
              <p class="form-note">已选择 {{ optimizationForm.case_paths.length }} 个 Case；基线和候选默认各运行 3 次，灰区自动追加到 5/7 次。</p>
            </template>

            <template v-else>
              <div class="form-two-columns">
                <label>Optimizer<select v-model="optimizationForm.optimizer_runner"><option value="claude">claude</option><option value="opencode">OpenCode</option></select></label>
                <label>可执行文件<input v-model.trim="optimizationForm.optimizer_executable" /></label>
              </div>
              <label>优化指令<textarea v-model.trim="optimizationForm.optimizer_instruction" rows="4"></textarea></label>
              <div class="form-two-columns">
                <label>评分 Judge<select v-model="optimizationForm.judge_runner"><option value="claude">claude</option><option value="opencode">OpenCode</option><option value="lexical">Lexical（调试）</option></select></label>
                <label>最大 Epoch<input v-model.number="optimizationForm.max_epochs" type="number" min="1" max="5" /></label>
              </div>
              <div class="form-three-columns">
                <label>最小分数提升<input v-model.number="optimizationForm.min_overall_delta" type="number" step="0.1" /></label>
                <label>最大耗时增长<input v-model.number="optimizationForm.max_latency_growth" type="number" step="0.05" /></label>
                <label>最大 Token 增长<input v-model.number="optimizationForm.max_token_growth" type="number" step="0.05" /></label>
              </div>
              <div class="api-notice"><IconInfoCircle :size="16" />每轮生成两个候选，Screening 只保留一个；连续两轮无候选或无提升会 Early Stop。</div>
            </template>

            <div class="dialog-actions">
              <button class="ghost-button" @click="showOptimizationDialog = false">取消</button>
              <button v-if="optimizationDialogStep > 1" class="ghost-button" @click="optimizationDialogStep -= 1">上一步</button>
              <button v-if="optimizationDialogStep < 3" class="primary-button" @click="optimizationDialogStep += 1">下一步</button>
              <button v-else class="primary-button" :disabled="optimizationSaving" @click="createOptimizationExperiment"><IconLoader2 v-if="optimizationSaving" class="spin" :size="15" />{{ optimizationSaving ? '正在创建…' : '创建并启动' }}</button>
            </div>
          </section>
        </div>
      </section>

      <!-- Settings view -->
      <section v-if="activeView === 'settings'" class="work-page settings-page">
        <div class="work-heading"><div><p class="eyebrow">SETTINGS</p><h1>设置</h1><p>配置结果路径、Harness 和模型；测评时直接选择需要运行的组合。</p></div><button class="ghost-button" @click="loadAppSettings(); loadEvaluationMethods(); loadEvaluationCatalog()"><IconRefresh :size="16" />刷新</button></div>
        <section class="surface form-card">
          <div class="panel-heading"><h2>结果路径配置</h2></div>
          <label>临时结果目录<span>单次评测默认输出到此目录</span><input v-model="appSettings.results_tmp_path" placeholder="data/results/tmp" /></label>
          <label>正式结果集目录<span>归档后的结果存入此目录（按 测试集/分类/case/时间戳 组织）</span><input v-model="appSettings.results_formal_path" placeholder="data/results" /></label>
          <button class="primary-button wide" @click="saveAppSettings"><IconSettings :size="16" />保存设置</button>
        </section>
        <section class="surface form-card method-card">
          <div class="panel-heading"><h2>旧测评方式</h2><button class="primary-button" @click="openMethodDialog"><IconPlus :size="16" />新建方式</button></div>
          <p class="form-note">命令在隔离目录执行，仅能看到本次复制的原始日志。标准输出会保存为对应的 Markdown 报告。</p>
          <div v-if="visibleEvaluationMethods.length" class="method-list">
            <div v-for="method in visibleEvaluationMethods" :key="method.id" class="method-row">
              <div><strong>{{ method.key }} <small>v{{ method.version }}</small></strong><code>{{ method.command_template }}</code><span v-if="method.tool_dir">工具目录：{{ method.tool_dir }}</span></div>
              <span :class="method.status === 'frozen' ? 'tag-match' : method.status === 'archived' ? 'tag-missing' : 'tag-partial'">{{ method.status }}</span>
              <span :class="method.probe?.available ? 'tag-match' : 'tag-partial'">{{ method.probe?.available ? '命令可用' : '未检测' }}</span>
              <button v-if="method.status === 'draft'" class="ghost-button" @click="probeEvaluationMethod(method)">检测</button>
              <button v-if="method.status === 'draft'" class="primary-button" :disabled="!method.probe?.available" @click="freezeEvaluationMethod(method)">冻结</button>
              <button class="ghost-button" @click="openMethodDialog(method)">修改</button>
              <button class="tree-delete" title="删除" @click="deleteEvaluationMethod(method)"><IconTrash :size="14" /></button>
            </div>
          </div>
          <div v-else class="empty-state"><IconTerminal2 :size="26" /><p>暂无测评方式</p><span>新建并检测、冻结后，即可在结果页提交测评。</span></div>
        </section>
        <section class="surface form-card method-card">
          <div class="panel-heading"><h2>Harness</h2><button class="primary-button" @click="openHarnessDialog"><IconPlus :size="16" />新建 Harness</button></div>
          <p class="form-note">定义 Harness 命令与共享并发；命令包含 <code>{model}</code> 时自动启用模型选择。</p>
          <div v-if="visibleEvaluationHarnesses.length" class="method-list">
            <div v-for="harness in visibleEvaluationHarnesses" :key="harness.id" class="method-row">
              <div><strong>{{ harness.key }} <small>v{{ harness.version }}</small></strong><code>{{ harness.command_template }}</code></div>
              <span :class="harness.status === 'frozen' ? 'tag-match' : harness.status === 'archived' ? 'tag-missing' : 'tag-partial'">{{ harness.status }}</span>
              <span :class="harness.probe?.available ? 'tag-match' : harness.probe?.checked_at ? 'tag-missing' : 'tag-partial'">{{ harness.probe?.available ? '命令可用' : harness.probe?.checked_at ? '命令不可用' : '未检测' }}</span>
              <button v-if="harness.status === 'draft'" class="ghost-button" :disabled="harnessActionId === harness.id" @click="probeEvaluationHarness(harness)">{{ harnessActionId === harness.id && harnessAction === 'probe' ? '检测中…' : '检测' }}</button>
              <button v-if="harness.status === 'draft'" class="primary-button" :disabled="harnessActionId === harness.id" @click="freezeEvaluationHarness(harness)">{{ harnessActionId === harness.id && harnessAction === 'freeze' ? '冻结中…' : '冻结' }}</button>
              <button class="ghost-button" @click="openHarnessDialog(harness)">修改</button>
              <button class="tree-delete" title="删除 Harness" :disabled="catalogActionId === harness.id" @click="deleteEvaluationHarness(harness)"><IconTrash :size="14" /></button>
            </div>
          </div>
          <div v-else class="empty-state"><IconTerminal2 :size="26" /><p>暂无 Harness</p><span>先新建一个脚本或 Agent Harness。</span></div>
        </section>
        <section class="surface form-card method-card">
          <div class="panel-heading"><h2>模型</h2><button class="primary-button" @click="openModelDialog"><IconPlus :size="16" />新建模型</button></div>
          <p class="form-note">这里只保存传给 Harness 的模型名称，不管理 endpoint、密钥或模型参数。</p>
          <div v-if="visibleEvaluationModels.length" class="method-list">
            <div v-for="model in visibleEvaluationModels" :key="model.id" class="method-row">
              <div><strong>{{ model.key }} <small>v{{ model.version }}</small></strong><code>--model {{ model.argument }}</code></div>
              <span :class="model.status === 'frozen' ? 'tag-match' : 'tag-missing'">{{ model.status }}</span>
              <button class="tree-delete" title="删除模型" :disabled="catalogActionId === model.id" @click="deleteEvaluationModel(model)"><IconTrash :size="14" /></button>
            </div>
          </div>
          <div v-else class="empty-state"><IconInfoCircle :size="26" /><p>暂无模型</p></div>
        </section>
      </section>
    </main>

    <div v-if="loading" class="busy-layer"><IconLoader2 :size="25" class="spin" />正在与 AnalystBench API 通信…</div>
    <div v-if="toast" class="toast"><IconAlertCircle :size="17" />{{ toast }}</div>

    <!-- Submit evaluation dialog -->
    <div v-if="showSubmitEvaluationDialog" class="dialog-overlay" @click.self="showSubmitEvaluationDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>提交测评</h2><span>步骤 {{ submissionStep }}/3</span></div>
        <p class="form-note">选择本次要测评的 Case。缺少有效日志的 Case 不会进入批次，也不会影响其他 Case。</p>
        <template v-if="submissionStep === 1">
          <label>测试集
            <select v-model="submissionForm.dataset_key" @change="resetSubmissionCaseSelection">
              <option value="" disabled>请选择测试集</option>
              <option v-for="testSet in localCaseTree" :key="testSet.key" :value="testSet.key">{{ testSet.name }}</option>
            </select>
          </label>
          <fieldset class="method-picker submission-case-picker">
            <legend>Case 选择</legend>
            <div class="submission-case-picker-head">
              <strong>{{ submissionForm.case_paths.length }}/{{ selectableSubmissionCases.length }} 个可测 Case 已选择</strong>
              <span>
                <button type="button" class="text-button" @click="selectAllReadySubmissionCases">全选可测</button>
                <button type="button" class="text-button" @click="clearSubmissionCaseSelection">清空</button>
              </span>
            </div>
            <label
              v-for="caseItem in selectedSubmissionCases"
              :key="`${caseItem.category}/${caseItem.key}`"
              :class="['compact-list-row', 'submission-case-option', { disabled: !caseItem.case_data?.submission_ready }]"
            >
              <input
                v-model="submissionForm.case_paths"
                type="checkbox"
                :value="submissionCasePath(caseItem)"
                :disabled="!caseItem.case_data?.submission_ready"
              />
              <span class="submission-case-path">{{ caseItem.category }}/{{ caseItem.key }}</span>
              <span :class="caseItem.case_data?.submission_ready ? 'tag-match' : 'tag-missing'">{{ caseItem.case_data?.submission_ready ? `${caseItem.case_data.log_count} 个日志` : '缺少有效日志' }}</span>
            </label>
            <p v-if="unavailableSubmissionCases.length" class="form-note">{{ unavailableSubmissionCases.length }} 个 Case 因日志未就绪自动跳过。</p>
          </fieldset>
        </template>
        <fieldset v-else-if="submissionStep === 2" class="method-picker">
          <template v-if="evaluationSelectionGroups.length">
            <legend>Harness × 模型（默认全选）</legend>
            <div v-for="group in evaluationSelectionGroups" :key="group.harness.id" class="target-picker-group">
              <strong>{{ group.harness.key }} <small>v{{ group.harness.version }}</small></strong>
              <label v-for="option in group.options" :key="option.key" class="check-row">
                <input v-model="submissionForm.target_selection_keys" type="checkbox" :value="option.key" />
                <span><strong>{{ option.model?.name || '无模型基线' }}</strong><code>{{ option.model?.argument || 'script-only' }}</code></span>
              </label>
            </div>
          </template>
          <template v-else>
            <legend>旧测评方式</legend>
            <label v-for="method in frozenEvaluationMethods" :key="method.id" class="check-row">
              <input v-model="submissionForm.method_ids" type="checkbox" :value="method.id" />
              <span><strong>{{ method.key }} v{{ method.version }}</strong><code>{{ method.command_template }}</code></span>
            </label>
            <p v-if="!frozenEvaluationMethods.length" class="form-note">没有可用方式，请先到设置页新建、检测并冻结。</p>
          </template>
        </fieldset>
        <template v-else>
          <div class="case-info-grid">
            <div class="case-info-item"><span class="case-info-label">测试集</span><span class="case-info-value">{{ submissionForm.dataset_key }}</span></div>
            <div class="case-info-item"><span class="case-info-label">本次 Case 数</span><span class="case-info-value">{{ submissionForm.case_paths.length }}</span></div>
            <div class="case-info-item"><span class="case-info-label">未纳入 Case</span><span class="case-info-value">{{ selectedSubmissionCases.length - submissionForm.case_paths.length }}</span></div>
            <div class="case-info-item"><span class="case-info-label">运行组合数</span><span class="case-info-value">{{ submissionForm.target_selection_keys.length || submissionForm.method_ids.length }}</span></div>
            <div class="case-info-item"><span class="case-info-label">生成任务数</span><span class="case-info-value">{{ submissionForm.case_paths.length * (submissionForm.target_selection_keys.length || submissionForm.method_ids.length) }}</span></div>
          </div>
          <label>打分 Judge
            <select v-model="submissionForm.judge_runner">
              <option value="claude">claude（推荐）</option>
              <option value="opencode">opencode</option>
            </select>
          </label>
        </template>
        <div class="dialog-actions">
          <button v-if="submissionStep === 1" class="ghost-button" @click="showSubmitEvaluationDialog = false">取消</button>
          <button v-else class="ghost-button" @click="submissionStep -= 1">上一步</button>
          <button v-if="submissionStep < 3" class="primary-button" @click="advanceSubmissionStep">下一步<IconChevronRight :size="16" /></button>
          <button v-else class="primary-button" :disabled="submissionRunning || !submissionForm.dataset_key || (!submissionForm.target_selection_keys.length && !submissionForm.method_ids.length)" @click="createEvaluationSubmission"><IconFlask :size="16" />{{ submissionRunning ? '提交中…' : '开始测评' }}</button>
        </div>
      </section>
    </div>

    <!-- Evaluation method dialog -->
    <div v-if="showMethodDialog" class="dialog-overlay" @click.self="showMethodDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>{{ editingMethodId ? '修改测评方式' : '新建测评方式' }}</h2></div>
        <p class="form-note">
          <template v-if="editingMethodId">修改会创建一个新的草稿版本，旧版本继续可用；新版本检测成功并冻结后即可用于测评。</template>
          <template v-else>支持 {input}、{input_dir}、{workspace}、{tool_dir}。命令不经过 Shell，不能使用管道或重定向。</template>
        </p>
        <label>Key
          <span>同时作为列表名称和报告文件名；支持大小写字母、数字、点、括号、-、_</span>
          <input v-model="methodForm.key" :disabled="Boolean(editingMethodId)" placeholder="claude(glm5.1)-native" />
        </label>
        <label>工具目录（可选）
          <input v-model="methodForm.tool_dir" placeholder="/home/user/evaluation-tools" />
        </label>
        <label>命令模板
          <textarea v-model="methodForm.command_template" rows="3" placeholder='claude -p "帮我分析日志 {input}"'></textarea>
        </label>
        <div class="form-grid">
          <label>超时（秒）<input v-model.number="methodForm.timeout_seconds" type="number" min="1" max="7200" /></label>
          <label>最大输出（字节）<input v-model.number="methodForm.max_output_bytes" type="number" min="1024" /></label>
          <label>并发限制<input v-model.number="methodForm.concurrency_limit" type="number" min="1" max="32" /></label>
        </div>
        <div class="dialog-actions">
          <button class="ghost-button" @click="showMethodDialog = false">取消</button>
          <button class="primary-button" :disabled="methodSaving" @click="createEvaluationMethod"><IconPlus :size="16" />{{ methodSaving ? '保存中…' : editingMethodId ? '保存为新版本并检测' : '创建并检测' }}</button>
        </div>
      </section>
    </div>

    <div v-if="showHarnessDialog" class="dialog-overlay" @click.self="showHarnessDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>{{ editingHarnessId ? '修改 Harness' : '新建 Harness' }}</h2></div>
        <p class="form-note">命令含 <code>{model}</code> 时需要模型，不含则作为无模型基线；命令不经过 Shell。</p>
        <label>Key<input v-model="harnessForm.key" :disabled="Boolean(editingHarnessId)" placeholder="claude-native" /></label>
        <label>命令模板<textarea v-model="harnessForm.command_template" rows="3" placeholder='claude -p "分析 {input}" --model {model}'></textarea></label>
        <div class="form-grid"><label>超时时间（秒）<input v-model.number="harnessForm.timeout_seconds" type="number" min="1" max="7200" /></label><label>并发数<input v-model.number="harnessForm.concurrency_limit" type="number" min="1" max="32" /></label></div>
        <div class="dialog-actions"><button class="ghost-button" @click="showHarnessDialog = false">取消</button><button class="primary-button" :disabled="harnessSaving" @click="saveEvaluationHarness">{{ harnessSaving ? '保存中…' : '保存并检测' }}</button></div>
      </section>
    </div>

    <div v-if="showModelDialog" class="dialog-overlay" @click.self="showModelDialog = false">
      <section class="surface dialog-card">
        <div class="panel-heading"><h2>新建模型</h2></div>
        <p class="form-note">模型名称会直接作为传给 Harness 的 <code>--model</code> 参数。</p>
        <label>模型名称<input v-model="modelForm.key" placeholder="glm5.1" /></label>
        <div class="dialog-actions"><button class="ghost-button" @click="showModelDialog = false">取消</button><button class="primary-button" :disabled="modelSaving" @click="saveEvaluationModel">{{ modelSaving ? '保存中…' : '保存模型' }}</button></div>
      </section>
    </div>

    <!-- Evaluation schedule dialog -->
    <div v-if="showScheduleDialog" class="dialog-overlay" @click.self="showScheduleDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>{{ editingScheduleId ? '编辑定时测评' : '新建定时测评' }}</h2><span>每天固定时间</span></div>
        <p class="form-note">到时间后由 Local Worker 创建普通测评批次。机器停机时恢复后只补跑最近一次。</p>
        <label>计划名称
          <input v-model="scheduleForm.name" placeholder="夜间回归" />
        </label>
        <div class="form-grid schedule-time-grid">
          <label>测试集
            <select v-model="scheduleForm.dataset_key" @change="resetScheduleCaseSelection">
              <option value="" disabled>请选择测试集</option>
              <option v-for="testSet in localCaseTree" :key="testSet.key" :value="testSet.key">{{ testSet.name }}</option>
            </select>
          </label>
          <label>每日时间
            <input v-model="scheduleForm.local_time" type="time" />
          </label>
          <label>时区
            <input v-model="scheduleForm.timezone" placeholder="Asia/Shanghai" />
          </label>
        </div>
        <label>Case 范围
          <select v-model="scheduleForm.case_mode" @change="scheduleForm.case_paths = []">
            <option value="all_ready">每次动态选择全部日志就绪 Case</option>
            <option value="selected">固定选择 Case</option>
          </select>
        </label>
        <fieldset v-if="scheduleForm.case_mode === 'selected'" class="method-picker schedule-case-picker">
          <legend>固定 Case</legend>
          <label
            v-for="caseItem in selectedScheduleCases"
            :key="`${caseItem.category}/${caseItem.key}`"
            :class="['compact-list-row', 'submission-case-option', { disabled: !caseItem.case_data?.submission_ready }]"
          >
            <input
              v-model="scheduleForm.case_paths"
              type="checkbox"
              :value="scheduleCasePath(caseItem)"
              :disabled="!caseItem.case_data?.submission_ready"
            />
            <span class="submission-case-path">{{ caseItem.category }}/{{ caseItem.key }}</span>
            <span :class="caseItem.case_data?.submission_ready ? 'tag-match' : 'tag-missing'">{{ caseItem.case_data?.submission_ready ? `${caseItem.case_data.log_count} 个日志` : '缺少有效日志' }}</span>
          </label>
        </fieldset>
        <fieldset v-if="evaluationSelectionGroups.length" class="method-picker">
          <legend>Harness × 模型（保存时固定当前版本）</legend>
          <div v-for="group in evaluationSelectionGroups" :key="group.harness.id" class="target-picker-group">
            <strong>{{ group.harness.key }} <small>v{{ group.harness.version }}</small></strong>
            <label v-for="option in group.options" :key="option.key" class="check-row">
              <input v-model="scheduleForm.target_selection_keys" type="checkbox" :value="option.key" />
              <span><strong>{{ option.model?.name || '无模型基线' }}</strong><code>{{ option.model?.argument || 'script-only' }}</code></span>
            </label>
          </div>
        </fieldset>
        <fieldset v-else class="method-picker">
          <legend>旧测评方式（固定到当前版本）</legend>
          <label v-for="method in frozenEvaluationMethods" :key="method.id" class="check-row">
            <input v-model="scheduleForm.method_ids" type="checkbox" :value="method.id" />
            <span><strong>{{ method.key }} v{{ method.version }}</strong><code>{{ method.command_template }}</code></span>
          </label>
        </fieldset>
        <div class="form-grid schedule-time-grid">
          <label>打分 Judge
            <select v-model="scheduleForm.judge_runner">
              <option value="claude">claude（推荐）</option>
              <option value="opencode">opencode</option>
            </select>
          </label>
          <label class="schedule-enabled-check">
            <input v-model="scheduleForm.enabled" type="checkbox" />
            <span>保存后启用计划</span>
          </label>
        </div>
        <div class="dialog-actions">
          <button class="ghost-button" @click="showScheduleDialog = false">取消</button>
          <button class="primary-button" :disabled="scheduleSaving" @click="saveEvaluationSchedule"><IconCircleCheck :size="16" />{{ scheduleSaving ? '保存中…' : '保存计划' }}</button>
        </div>
      </section>
    </div>

    <!-- Evaluation schedule runs dialog -->
    <div v-if="showScheduleRunsDialog" class="dialog-overlay" @click.self="showScheduleRunsDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>计划执行历史</h2><span>{{ scheduleRunsTitle }}</span></div>
        <div v-if="scheduleRuns.length" class="schedule-run-list">
          <div v-for="run in scheduleRuns" :key="run.id" class="schedule-run-row">
            <div>
              <strong>{{ formatScheduleDateTime(run.scheduled_for, run.config.timezone) }}</strong>
              <small>{{ run.trigger_type }}<template v-if="run.submission_timestamp"> · 批次 {{ run.submission_timestamp }}</template></small>
            </div>
            <span :class="scheduleStatusClass(run.status)">{{ run.status }}</span>
            <small v-if="run.error?.message" class="tone-warning">{{ run.error.message }}</small>
          </div>
        </div>
        <div v-else class="empty-state"><IconInfoCircle :size="26" /><p>暂无执行记录</p></div>
        <div class="dialog-actions"><button class="primary-button" @click="showScheduleRunsDialog = false">关闭</button></div>
      </section>
    </div>

    <!-- Method run artifacts dialog -->
    <div v-if="showArtifactDialog" class="dialog-overlay" @click.self="showArtifactDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>方式运行审计</h2><span>{{ methodArtifactView?.status }} · attempt {{ methodArtifactView?.attempt }}</span></div>
        <p v-if="methodArtifactView?.message" class="form-note tone-warning">{{ methodArtifactView.message }}</p>
        <div class="artifact-timing">
          <span>开始<strong>{{ formatMethodTimestamp(methodArtifactView?.started_at) }}</strong></span>
          <span>结束<strong>{{ formatMethodTimestamp(methodArtifactView?.finished_at) }}</strong></span>
          <span>执行耗时<strong>{{ formatDuration(methodArtifactView?.duration_ms) }}</strong></span>
        </div>
        <label>命令
          <div class="code-block">{{ (methodArtifactView?.command || []).join(' ') }}</div>
        </label>
        <label>stdout
          <div class="code-block">{{ methodArtifactView?.stdout || '（空）' }}</div>
        </label>
        <label>stderr
          <div class="code-block">{{ methodArtifactView?.stderr || '（空）' }}</div>
        </label>
        <div class="dialog-actions"><button class="primary-button" @click="showArtifactDialog = false">关闭</button></div>
      </section>
    </div>

    <div v-if="showTargetComparisonDialog" class="dialog-overlay" @click.self="showTargetComparisonDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>Harness / 模型组合对比</h2><span :class="targetComparison?.controlled ? 'tag-match' : 'tag-missing'">{{ targetComparison?.controlled ? '受控' : '非受控' }}</span></div>
        <p v-for="warning in targetComparison?.warnings || []" :key="warning" class="form-note tone-warning">{{ warning }}</p>
        <div class="target-comparison-grid">
          <article v-for="item in targetComparison?.targets || []" :key="item.target.key" class="target-metric-card">
            <strong>{{ item.target.display_name }}</strong><small>{{ item.target.key }}</small>
            <span>平均得分 <b>{{ item.average_score === null ? '—' : item.average_score.toFixed(1) }}</b></span>
            <span>通过率 <b>{{ item.pass_rate === null ? '—' : `${(item.pass_rate * 100).toFixed(0)}%` }}</b></span>
            <span>成功率 <b>{{ `${(item.generation_success_rate * 100).toFixed(0)}%` }}</b></span>
            <span>中位 / P95 <b>{{ formatDuration(item.median_duration_ms) }} / {{ formatDuration(item.p95_duration_ms) }}</b></span>
            <small>覆盖 {{ item.scored_case_count }}/{{ item.requested_case_count }} · 耗时样本 {{ item.duration_sample_count }}</small>
          </article>
        </div>
        <div v-if="targetComparison?.by_harness?.length || targetComparison?.by_model?.length" class="comparison-groups">
          <p v-for="group in targetComparison.by_harness" :key="`h-${group.key}`"><strong>固定 Harness {{ group.key }}</strong>：{{ group.target_keys.join(' vs ') }}</p>
          <p v-for="group in targetComparison.by_model" :key="`m-${group.key}`"><strong>固定模型 {{ group.key }}</strong>：{{ group.target_keys.join(' vs ') }}</p>
        </div>
        <div v-if="targetComparison?.pairwise?.length" class="comparison-groups">
          <p v-for="pair in targetComparison.pairwise" :key="`${pair.group}-${pair.baseline}-${pair.candidate}`"><strong>{{ pair.baseline }} → {{ pair.candidate }}</strong>：共同 Case {{ pair.shared_scored_case_count }}，平均得分差 {{ pair.average_score_delta === null ? '—' : pair.average_score_delta.toFixed(1) }}</p>
        </div>
        <p v-else class="form-note">选择至少两个共享同一 Harness 或同一模型的组合后，会显示成对比较。</p>
        <div class="dialog-actions"><button class="primary-button" @click="showTargetComparisonDialog = false">关闭</button></div>
      </section>
    </div>

    <!-- Move/Promote dialog -->
    <div v-if="showMoveDialog" class="dialog-overlay" @click.self="showMoveDialog = false">
      <section class="surface dialog-card">
        <div class="panel-heading"><h2>{{ moveDialogMode === 'promote' ? '归档到正式结果集' : '移动结果' }}</h2><span>{{ moveDialogItem?.id }}</span></div>
        <p class="form-note">选择目标 Case（只移动结果，不移动 case.json）</p>
        <label>测试集
          <select v-model="moveForm.test_set" @change="onMoveTestSetChange">
            <option value="" disabled>请选择测试集</option>
            <option v-for="ts in moveTestSetOptions" :key="ts.key" :value="ts.key">{{ ts.name }}</option>
          </select>
        </label>
        <label>问题分类
          <select v-model="moveForm.category" @change="onMoveCategoryChange" :disabled="!moveForm.test_set">
            <option value="" disabled>请选择分类</option>
            <option v-for="cat in moveCategoryOptions" :key="cat.key" :value="cat.key">{{ cat.name }}</option>
          </select>
        </label>
        <label>Case 目录
          <select v-model="moveForm.case_dir" :disabled="!moveForm.category">
            <option value="" disabled>请选择 Case</option>
            <option v-for="cs in moveCaseDirOptions" :key="cs.key" :value="cs.key">{{ cs.name }}</option>
          </select>
        </label>
        <div class="dialog-actions">
          <button class="ghost-button" @click="showMoveDialog = false">取消</button>
          <button class="primary-button" @click="confirmMoveDialog" :disabled="!moveForm.test_set || !moveForm.category || !moveForm.case_dir"><IconCloudUpload v-if="moveDialogMode === 'promote'" :size="16" /><IconChevronRight v-else :size="16" />{{ moveDialogMode === 'promote' ? '归档' : '移动' }}</button>
        </div>
      </section>
    </div>

    <!-- Evaluate dialog -->
    <div v-if="showEvaluateDialog" class="dialog-overlay" @click.self="showEvaluateDialog = false">
      <section class="surface dialog-card">
        <div class="panel-heading"><h2>评分</h2><span>{{ selectedLocalCasePath }}</span></div>
        <p class="form-note">选择一份或多份 AI 日志报告，对该 Case 进行评分。评分结果会保存到临时结果目录。</p>
        <label>评分引擎
          <select v-model="evaluateForm.judge">
            <option value="lexical">词法评分（lexical，最快）</option>
            <option value="claude">语义评分（claude，需 LLM）</option>
            <option value="opencode">语义评分（opencode，需 LLM）</option>
          </select>
        </label>
        <label>日志文件
          <input type="file" multiple accept=".md,.json,.txt" @change="onEvaluateFileChange" />
          <span v-if="evaluateFiles.length" class="file-count">{{ evaluateFiles.length }} 份已选择</span>
        </label>
        <div class="dialog-actions">
          <button class="ghost-button" @click="showEvaluateDialog = false">取消</button>
          <button class="primary-button" @click="runEvaluate" :disabled="!evaluateFiles.length || evaluateRunning"><IconFlask :size="16" />{{ evaluateRunning ? '评分中…' : '开始评分' }}</button>
        </div>
      </section>
    </div>

    <!-- Create Case from text dialog -->
    <div v-if="showCreateCaseDialog" class="dialog-overlay" @click.self="showCreateCaseDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>从文本创建 Case</h2></div>
        <p class="form-note">输入参考答案和问题描述，LLM 将自动转换为 Case JSON 并生成评分项。审核确认后发布到测试集。</p>
        <label>Case Key（必填）
          <input v-model="caseCreateForm.case_key" placeholder="chmod_hung" required />
        </label>
        <label>测试集（必填）
          <input v-model="caseCreateForm.test_set" placeholder="kdiag" required />
        </label>
        <label>问题分类（必填）
          <input v-model="caseCreateForm.category" placeholder="SYSTEM_DEADLOCK" required />
        </label>
        <label>问题描述（可选，LLM 可从参考答案自动推导）
          <input v-model="caseCreateForm.problem_statement" placeholder="系统出现死锁重启…" />
        </label>
        <label>参考答案（必填）
          <textarea v-model="caseCreateForm.reference_answer" rows="8" placeholder="日志1：file_setattr&#10;结论1：chmod 进程卡在 file_setattr&#10;分类：SYSTEM_DEADLOCK&#10;根因：clusterapp 服务拉起时调用 chmod…" style="min-height:120px;resize:vertical"></textarea>
        </label>
        <label>原始日志（可选，可稍后在 Case 详情补充）
          <input type="file" multiple @change="onCaseCreateLogFileChange" />
          <span v-if="caseCreateLogFiles.length" class="file-count">{{ caseCreateLogFiles.length }} 份已选择</span>
        </label>
        <div class="dialog-actions">
          <button class="ghost-button" @click="showCreateCaseDialog = false">取消</button>
          <button class="primary-button" @click="submitCreateCase" :disabled="!caseCreateForm.reference_answer.trim() || !caseCreateForm.case_key.trim() || !caseCreateForm.test_set.trim() || !caseCreateForm.category.trim() || caseCreateRunning"><IconSparkles :size="16" />{{ caseCreateRunning ? '生成中…' : '生成 Case' }}</button>
        </div>
      </section>
    </div>

    <!-- Case Draft Review dialog -->
    <div v-if="showCaseReviewDialog" class="dialog-overlay" @click.self="showCaseReviewDialog = false">
      <section class="surface dialog-card dialog-card-wide">
        <div class="panel-heading"><h2>审核 Case</h2><span :class="caseDraftView?.status === 'generating' ? '' : caseDraftView?.status === 'needs_confirmation' ? 'tag-partial' : caseDraftView?.status === 'ready' ? 'tag-match' : caseDraftView?.status === 'published' ? 'tag-match' : caseDraftView?.status === 'failed' ? 'tag-missing' : ''">{{ caseDraftView?.status === 'generating' ? '生成中' : caseDraftView?.status === 'needs_confirmation' ? '待确认' : caseDraftView?.status === 'ready' ? '已审核' : caseDraftView?.status === 'published' ? '已发布' : caseDraftView?.status === 'failed' ? '失败' : caseDraftView?.status }}</span></div>
        <template v-if="caseDraftView?.status === 'generating'">
          <div class="empty-state"><IconLoader2 :size="24" class="spin" /><p>LLM 正在生成 Case JSON…</p></div>
        </template>
        <template v-else-if="caseDraftView?.status === 'needs_confirmation'">
          <p class="form-note">Case JSON 已生成，以下问题需要确认。点击"全部确认"自动采纳建议值。</p>
          <div class="case-review-questions">
            <div v-for="q in caseDraftView.questions" :key="q.id" class="case-review-q">
              <strong>{{ q.field_path }}</strong>
              <p>{{ q.question }}</p>
              <span v-if="q.current_value != null" class="form-note">当前值：{{ JSON.stringify(q.current_value) }}</span>
              <span v-if="q.suggested_value != null" class="form-note tone-info">建议值：{{ JSON.stringify(q.suggested_value) }}</span>
            </div>
          </div>
          <div class="dialog-actions">
            <button class="ghost-button" @click="rejectCaseDraft">取消</button>
            <button class="primary-button" @click="approveCaseDraft"><IconCircleCheck :size="16" />全部确认</button>
          </div>
        </template>
        <template v-else-if="caseDraftView?.status === 'ready'">
          <p class="form-note">Case 已审核通过，点击"发布"将其写入测试集目录。</p>
          <div class="case-info-grid">
            <div class="case-info-item"><span class="case-info-label">Case Key</span><span class="case-info-value">{{ caseDraftView.case_key }}</span></div>
            <div class="case-info-item"><span class="case-info-label">测试集</span><span class="case-info-value">{{ caseDraftView.test_set }}</span></div>
            <div class="case-info-item"><span class="case-info-label">问题分类</span><span class="case-info-value">{{ caseDraftView.category }}</span></div>
            <div class="case-info-item"><span class="case-info-label">评分项数</span><span class="case-info-value">{{ caseDraftView.summary.claim_count }}</span></div>
          </div>
          <div class="dialog-actions">
            <button class="ghost-button" @click="rejectCaseDraft">取消</button>
            <button class="primary-button" @click="approveCaseDraft"><IconCloudUpload :size="16" />发布到测试集</button>
          </div>
        </template>
        <template v-else-if="caseDraftView?.status === 'published'">
          <p class="form-note tone-success">Case 已成功发布到测试集！</p>
          <div class="case-info-grid">
            <div class="case-info-item"><span class="case-info-label">Case Key</span><span class="case-info-value">{{ caseDraftView.case_key }}</span></div>
            <div class="case-info-item"><span class="case-info-label">测试集</span><span class="case-info-value">{{ caseDraftView.test_set }}</span></div>
            <div class="case-info-item"><span class="case-info-label">问题分类</span><span class="case-info-value">{{ caseDraftView.category }}</span></div>
          </div>
          <div class="dialog-actions">
            <button class="primary-button" @click="showCaseReviewDialog = false; loadLocalCaseTree()">完成</button>
          </div>
        </template>
        <template v-else-if="caseDraftView?.status === 'failed'">
          <p class="form-note tone-warning">生成失败：{{ caseDraftView.error?.message ?? '未知错误' }}</p>
          <div class="dialog-actions">
            <button class="ghost-button" @click="showCaseReviewDialog = false">关闭</button>
          </div>
        </template>
      </section>
    </div>
  </div>
</template>
