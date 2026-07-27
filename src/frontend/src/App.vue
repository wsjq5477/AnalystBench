<script>
import appOptions from "./app-options";

export default appOptions;
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span>AnalystBench</span></div>
      <nav class="nav-list" aria-label="主导航">
        <button :class="['nav-item', { active: activeView === 'dashboard' }]" @click="navigate('dashboard')"><IconLayoutDashboard :size="19" />总览</button>
        <button :class="['nav-item', { active: activeView === 'dataset' }]" @click="navigate('dataset')"><IconDatabase :size="19" />测试集<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item', { active: activeView === 'results' }]" @click="navigate('results')"><IconClipboardData :size="19" />评测结果<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item muted', { active: activeView === 'settings' }]" @click="navigate('settings')"><IconSettings :size="19" />设置<IconChevronRight class="nav-arrow" :size="16" /></button>
      </nav>
      <div class="sidebar-foot"><span>© 2026 AnalystBench</span><span>v1.0.0</span><small :class="connection">{{ connection === 'connected' ? 'API 已连接' : connection === 'offline' ? 'API 未连接' : '正在检查 API' }}</small></div>
    </aside>

    <main class="content-area">
      <header class="app-header">
        <div class="breadcrumb"><span>{{ activeView === 'dashboard' ? '总览' : activeView === 'dataset' ? '测试集' : activeView === 'results' ? '评测结果' : '设置' }}</span></div>
        <div class="header-actions">
          <label class="date-input"><IconTerminal2 :size="15" /><input type="text" value="2025-06-22 ~ 2025-07-22" aria-label="时间范围" /></label>
          <button class="ghost-button"><IconFileExport :size="16" />导出报告</button>
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
          <button class="ghost-button" @click="loadDashboardData()"><IconRefresh :size="16" />刷新</button>
        </div>
        <section class="surface score-panel">
          <div class="panel-title">综合得分 <span>（越高越好）</span><IconInfoCircle :size="15" /></div>
          <div class="score-list">
            <article v-for="card in dashboardScoreCards" :key="card.label" class="score-item" :style="{ '--row-color': card.color }">
              <span>{{ card.label }}</span><strong>{{ card.score.toFixed(1) }}</strong><i><b :style="{ width: `${card.score}%` }" /></i>
            </article>
          </div>
        </section>

        <div class="chart-grid">
          <section class="surface chart-panel"><div class="panel-heading"><h2>按问题种类得分对比</h2><span v-if="dashboardLoaded" class="api-badge"><IconCircleCheck :size="14" />真实运行数据</span></div><ChartCanvas kind="bar" :labels="categoryBarLabels" :series="categoryBarSeries" :height="276" /></section>
          <section class="surface chart-panel"><div class="panel-heading"><h2>综合得分对比</h2></div><ChartCanvas kind="bar" :labels="['得分']" :series="dashboardScoreCards.map((c, idx) => ({ name: c.label, values: [c.score] }))" :height="276" /></section>
        </div>

        <section class="surface matrix-panel">
          <div class="panel-heading"><h2>按问题种类对比 <span>（各分类平均得分）</span></h2><button class="text-button" @click="navigate('results')">查看全部 <IconChevronRight :size="15" /></button></div>
          <div class="matrix-scroll"><table class="score-matrix"><thead><tr><th>方案</th><th v-for="cat in activeCategories" :key="cat.key">{{ cat.name }}<small v-if="cat.case_count > 1">（{{ cat.case_count }} case）</small></th><th>总平均</th></tr></thead><tbody><tr v-for="row in categoryComparisonRows" :key="row.name" :style="{ '--row-color': row.color }"><th class="row-color-dot"><i></i><span v-html="row.name.replace(/\n/g, '<br>')"></span></th><td v-for="(score, index) in row.categoryScores" :key="`${row.name}-${index}`" class="row-color-text">{{ score.toFixed(1) }}</td><td class="average row-color-text">{{ row.average.toFixed(1) }}</td></tr></tbody></table></div>
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
              <dl>
                <div><dt>Case Key</dt><dd>{{ (selectedLocalCaseData.case)?.case_key ?? '—' }}</dd></div>
                <div><dt>问题分类</dt><dd>{{ ((selectedLocalCaseData.case)?.category)?.name ?? '—' }}</dd></div>
                <div><dt>测试集</dt><dd>{{ ((selectedLocalCaseData.case)?.test_set)?.name ?? '—' }}</dd></div>
                <div><dt>评分项数</dt><dd>{{ ((selectedLocalCaseData.eval_spec_draft)?.claims)?.length ?? 0 }}</dd></div>
              </dl>
              <div class="code-block">{{ JSON.stringify(selectedLocalCaseData, null, 2) }}</div>
            </template>
            <div v-else class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧 Case 查看详情</p></div>
          </section>
        </div>
      </section>

      <section v-if="activeView === 'results'" class="work-page results-page">
        <div class="work-heading"><div><p class="eyebrow">EVALUATION RESULTS</p><h1>评测结果</h1><p>查看评测结果，临时结果可归档到正式结果集。</p></div><button class="ghost-button" @click="resultSource === 'tmp' ? refreshDirectResults() : refreshDirectResults()"><IconRefresh :size="16" />刷新</button></div>
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
              <p class="engine-note" style="color:#e5b96a">{{ String((selectedResultData.error)?.message ?? '未知错误') }}</p>
            </template>
            <template v-else-if="parseSummary(selectedResultData)">
              <div class="panel-heading"><h2>评测结果详情</h2><span>{{ String(selectedResultData.case_key ?? '') }} · {{ String(selectedResultData.id ?? '') }}</span></div>
              <p class="engine-note">{{ parseSummary(selectedResultData).engine_note }}</p>
              <section class="surface result-score-panel">
                <div class="panel-title">综合得分 <span>（越高越好）</span></div>
                <div class="result-score-list">
                  <div v-for="(report, idx) in resultRankedReports" :key="'sc-' + report.candidate_name" class="result-score-row" :style="{ '--row-color': resultColors[idx] }">
                    <span class="result-score-name"><i></i>{{ report.candidate_name }}</span>
                    <strong>{{ parseFloat(report.score).toFixed(1) }}</strong>
                    <i class="result-score-bar"><b :style="{ width: `${parseFloat(report.score)}%` }" /></i>
                    <span :class="report.passed ? 'tag-match' : 'tag-missing'">{{ report.passed ? '通过' : '未通过' }}</span>
                  </div>
                </div>
              </section>
              <section class="surface result-overview-section">
                <h3>总览</h3>
                <table class="result-overview-table">
                  <thead><tr><th>排名</th><th>报告</th><th>得分</th><th>结果</th><th>命中</th><th>缺失链</th></tr></thead>
                  <tbody>
                    <tr v-for="(report, idx) in parseSummary(selectedResultData).reports" :key="report.candidate_name">
                      <td>{{ idx + 1 }}</td>
                      <td :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</td>
                      <td>{{ parseFloat(report.score).toFixed(1) }}</td>
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
          <div v-if="directResultList.length === 0 && !resultLoading" class="empty-state surface" style="min-height:200px"><IconClipboardData :size="30" /><p>暂无正式评测结果</p><span>归档临时结果后即可在此查看。</span></div>
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
                      <div v-for="item in items" :key="item.id" class="result-tree-leaf-row">
                        <div :class="['result-tree-leaf', { selected: selectedResultId === item.id }]" @click="loadDirectResult(item)">
                          <span class="result-tree-leaf-name">{{ item.timestamp }}</span>
                          <span class="result-tree-leaf-meta"><span :class="item.status === 'completed' ? 'tag-match' : 'tag-missing'">{{ item.status }}</span></span>
                        </div>
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
                  <div class="panel-title">综合得分 <span>（越高越好）</span></div>
                  <div class="result-score-list">
                    <div v-for="(report, idx) in resultRankedReports" :key="'sc-' + report.candidate_name" class="result-score-row" :style="{ '--row-color': resultColors[idx] }">
                      <span class="result-score-name"><i></i>{{ report.candidate_name }}</span>
                      <strong>{{ parseFloat(report.score).toFixed(1) }}</strong>
                      <i class="result-score-bar"><b :style="{ width: `${parseFloat(report.score)}%` }" /></i>
                      <span :class="report.passed ? 'tag-match' : 'tag-missing'">{{ report.passed ? '通过' : '未通过' }}</span>
                    </div>
                  </div>
                </section>
                <section class="surface result-overview-section">
                  <h3>总览</h3>
                  <table class="result-overview-table">
                    <thead><tr><th>排名</th><th>报告</th><th>得分</th><th>结果</th><th>命中</th><th>缺失链</th></tr></thead>
                    <tbody>
                      <tr v-for="(report, idx) in parseSummary(selectedResultData).reports" :key="report.candidate_name">
                        <td>{{ idx + 1 }}</td>
                        <td :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</td>
                        <td>{{ parseFloat(report.score).toFixed(1) }}</td>
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

      <!-- Settings view -->
      <section v-if="activeView === 'settings'" class="work-page settings-page">
        <div class="work-heading"><div><p class="eyebrow">SETTINGS</p><h1>设置</h1><p>配置评测结果的存储路径。</p></div><button class="ghost-button" @click="loadAppSettings"><IconRefresh :size="16" />刷新</button></div>
        <section class="surface form-card">
          <div class="panel-heading"><h2>结果路径配置</h2></div>
          <label>临时结果目录<span>单次评测默认输出到此目录</span><input v-model="appSettings.results_tmp_path" placeholder="data/results/tmp" /></label>
          <label>正式结果集目录<span>归档后的结果存入此目录（按 测试集/分类/case/时间戳 组织）</span><input v-model="appSettings.results_formal_path" placeholder="data/results" /></label>
          <button class="primary-button wide" @click="saveAppSettings"><IconSettings :size="16" />保存设置</button>
        </section>
      </section>
    </main>

    <div v-if="loading" class="busy-layer"><IconLoader2 :size="25" class="spin" />正在与 AnalystBench API 通信…</div>
    <div v-if="toast" class="toast"><IconAlertCircle :size="17" />{{ toast }}</div>

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
            <option value="claude-code">语义评分（claude-code，需 LLM）</option>
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
              <span v-if="q.suggested_value != null" class="form-note" style="color:#8fa9ca">建议值：{{ JSON.stringify(q.suggested_value) }}</span>
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
          <p class="form-note" style="color:#74cc92">Case 已成功发布到测试集！</p>
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
          <p class="form-note" style="color:#e5b96a">生成失败：{{ caseDraftView.error?.message ?? '未知错误' }}</p>
          <div class="dialog-actions">
            <button class="ghost-button" @click="showCaseReviewDialog = false">关闭</button>
          </div>
        </template>
      </section>
    </div>
  </div>
</template>
