import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { color, font, radius, shadow, size, space } from "../../styles/tokens";
import { FRESHNESS_LABEL, classifyFreshness, formatBackendTimestamp } from "../../utils/freshness";
import { deriveVelocityDirection, VELOCITY_DIRECTION_LABEL } from "../../utils/velocity";
import { formatDashboardDate, formatDashboardNumber, formatDashboardUnits } from "./labels";
import FilterToolbar from "./FilterToolbar";
import Icon from "../ui/Icon";
import { ErrorState, SectionState, Skeleton } from "../ui/States";

const ACCENT = color.accent;
const NAVY = color.ink;
const TABLET_BEZEL_WIDTH = 182;
const TABLET_BEZEL_HEIGHT = 238;

export default function OverviewDashboard({
  project,
  area,
  summary,
  summaryLoading,
  summaryError,
  onSummaryRetry,
  market,
  marketLoading,
  marketError,
  trend,
  trendLoading,
  trendError,
  onTrendRetry,
  trendStatus,
  trendMessage,
  areas,
  areasLoading,
  areasError,
  onAreasRetry,
  dataQuality,
  dataQualityLoading,
  dataQualityError,
  onDataQualityRetry,
  refreshing,
  onRefresh,
  toolbarProjects,
  toolbarAreas,
  projectExternalId,
  areaExternalId,
  range,
  onProject,
  onArea,
  onRange,
  availableYears,
  selectedYear,
  onYear,
  customFrom,
  customTo,
  onCustomFrom,
  onCustomTo,
  onSelectArea,
  isWide,
  preview = false,
}) {
  const [metric, setMetric] = useState("velocity");
  const freshness = classifyFreshness(summary);
  const freshnessLabel = FRESHNESS_LABEL[freshness] || FRESHNESS_LABEL.timestamp_unknown;
  const velocityDirection = deriveVelocityDirection(summary?.velocity_7d, summary?.velocity_30d);
  const direction = VELOCITY_DIRECTION_LABEL[velocityDirection];
  const headerTitle = project?.name ? `${project.name} · Tổng quan` : "Tổng quan";
  const context = area?.area_name || area?.name || "Toàn bộ dự án";

  const workspace = (
    <OverviewWorkspace
      project={project}
      summary={summary}
      summaryLoading={summaryLoading}
      summaryError={summaryError}
      onSummaryRetry={onSummaryRetry}
      market={market}
      marketLoading={marketLoading}
      marketError={marketError}
      trend={trend}
      trendLoading={trendLoading}
      trendError={trendError}
      onTrendRetry={onTrendRetry}
      trendStatus={trendStatus}
      trendMessage={trendMessage}
      areas={areas}
      areasLoading={areasLoading}
      areasError={areasError}
      onAreasRetry={onAreasRetry}
      dataQuality={dataQuality}
      dataQualityLoading={dataQualityLoading}
      dataQualityError={dataQualityError}
      onDataQualityRetry={onDataQualityRetry}
      refreshing={refreshing}
      onRefresh={onRefresh}
      toolbarProjects={toolbarProjects}
      toolbarAreas={toolbarAreas}
      projectExternalId={projectExternalId}
      areaExternalId={areaExternalId}
      range={range}
      onProject={onProject}
      onArea={onArea}
      onRange={onRange}
      availableYears={availableYears}
      selectedYear={selectedYear}
      onYear={onYear}
      customFrom={customFrom}
      customTo={customTo}
      onCustomFrom={onCustomFrom}
      onCustomTo={onCustomTo}
      onSelectArea={onSelectArea}
      isWide={preview ? false : isWide}
      metric={metric}
      onMetric={setMetric}
      freshnessLabel={freshnessLabel}
      direction={direction}
      headerTitle={headerTitle}
      context={context}
      preview={preview}
    />
  );

  if (preview) return <div style={{ ...S.page, ...S.previewPage }}>{workspace}</div>;

  return (
    <div style={S.page}>
      <div style={{ ...S.deviceComposition, ...(isWide ? S.deviceCompositionWide : S.deviceCompositionNarrow) }}>
        <SalesPulse
          project={project}
          context={context}
          summary={summary}
          loading={summaryLoading}
          freshnessLabel={freshnessLabel}
          direction={direction}
          isWide={isWide}
        />
        <section style={{ ...S.tabletPreviewCell, ...(isWide ? null : S.tabletPreviewCellNarrow) }} aria-label="Không gian phân tích tổng quan">
          <TabletDevicePreview href={buildPreviewHref(projectExternalId, areaExternalId)} isWide={isWide} />
        </section>
      </div>
    </div>
  );
}

function OverviewWorkspace({
  project, summary, summaryLoading, summaryError, onSummaryRetry,
  market, marketLoading, marketError, trend, trendLoading, trendError,
  onTrendRetry, trendStatus, trendMessage, areas, areasLoading, areasError,
  onAreasRetry, dataQuality, dataQualityLoading, dataQualityError,
  onDataQualityRetry, refreshing, onRefresh, toolbarProjects, toolbarAreas,
  projectExternalId, areaExternalId, range, onProject, onArea, onRange,
  availableYears, selectedYear, onYear, customFrom, customTo, onCustomFrom,
  onCustomTo, onSelectArea, isWide, metric, onMetric, freshnessLabel,
  direction, headerTitle, context, preview,
}) {
  return (
    <div style={{ ...S.workspaceContent, ...(preview ? S.previewWorkspaceContent : null) }}>
      <header style={S.pageHeader}>
        <div>
          <div style={S.eyebrow}>TỔNG QUAN / {context.toUpperCase()}</div>
          <h1 style={S.title}>{headerTitle}</h1>
          <p style={S.subtitle}>Phạm vi {context.toLowerCase()} · tỷ lệ hấp thụ là số căn đã bán trên tổng quỹ căn. Dự báo: chưa khả dụng.</p>
        </div>
        <div style={S.headerActions}>
          <label style={S.search}>
            <Icon name="search" size={16} color={color.muted} />
            <input aria-label="Tìm kiếm trong dashboard" placeholder="Tìm kiếm  ⌘K" />
          </label>
          <span style={{ ...S.freshness, ...freshnessTone(freshnessLabel.tone) }} title={formatBackendTimestamp(summary?.last_successful_sync)}>
            <span style={S.freshnessDot} />{freshnessLabel.text}
          </span>
          <button type="button" style={S.iconButton} aria-label="Thông báo" title="Thông báo">
            <Icon name="bell" size={17} color={color.body} />
          </button>
          <span style={S.profile} aria-label="Tài khoản hiện tại">AI</span>
          <button type="button" style={S.refreshButton} onClick={onRefresh} disabled={refreshing}>
            <Icon name="refresh" size={15} />{refreshing ? "Đang tải…" : "Làm mới"}
          </button>
        </div>
      </header>

      <FilterToolbar
        projects={toolbarProjects}
        areas={toolbarAreas}
        projectId={projectExternalId}
        areaId={areaExternalId ?? "all"}
        range={range}
        onProject={onProject}
        onArea={onArea}
        onRange={onRange}
        availableYears={availableYears}
        selectedYear={selectedYear}
        onYear={onYear}
        customFrom={customFrom}
        customTo={customTo}
        onCustomFrom={onCustomFrom}
        onCustomTo={onCustomTo}
        loading={summaryLoading}
        showProjectSelector
      />

      <OverviewKpis summary={summary} loading={summaryLoading} error={summaryError} onRetry={onSummaryRetry} direction={direction} market={market} marketLoading={marketLoading} marketError={marketError} />
      <OverviewEvidence summary={summary} summaryLoading={summaryLoading} dataQuality={dataQuality} dataQualityLoading={dataQualityLoading} dataQualityError={dataQualityError} onDataQualityRetry={onDataQualityRetry} areas={areas} areasLoading={areasLoading} areasError={areasError} onAreasRetry={onAreasRetry} isWide={isWide} />

      <div style={{ ...S.mainGrid, ...(isWide ? null : S.mainGridNarrow) }}>
        <OverviewTrendChart series={trend} metric={metric} onMetric={onMetric} range={range} onRange={onRange} loading={trendLoading} error={trendError} onRetry={onTrendRetry} dataStatus={trendStatus} emptyMessage={trendMessage} />
        <PriorityAdvisory loading={areasLoading} error={areasError} onRetry={onAreasRetry} />
      </div>

      <OverviewBreakdown areas={areas} areasLoading={areasLoading} areasError={areasError} onAreasRetry={onAreasRetry} dataQuality={dataQuality} dataQualityLoading={dataQualityLoading} dataQualityError={dataQualityError} onDataQualityRetry={onDataQualityRetry} project={project} />
      <UnavailableAnalytics />
      <footer style={S.disclaimer}>Dữ liệu hiển thị từ backend hiện tại. Dự báo chưa khả dụng và không phải cam kết kết quả.</footer>
    </div>
  );
}

export function buildPreviewHref(projectExternalId, areaExternalId) {
  const query = new URLSearchParams();
  if (projectExternalId) query.set("project", projectExternalId);
  if (areaExternalId) query.set("area", areaExternalId);
  const suffix = query.toString();
  return `/preview/overview${suffix ? `?${suffix}` : ""}`;
}

function TabletDevicePreview({ href, isWide }) {
  const cellRef = useRef(null);
  const { width, height } = useResponsiveDeviceSize(cellRef, isWide);

  return (
    <device-mockup
      ref={cellRef}
      data-testid="overview-tablet-mockup"
      type="tablet"
      mode="iframe"
      href={href}
      alt="AbsorptionIQ tablet dashboard preview"
      screen-background="white"
      width={width ? String(width) : undefined}
      style={{
        ...S.tabletMockup,
        ...(width ? { width, height } : null),
      }}
    />
  );
}

function useResponsiveDeviceSize(ref, isWide) {
  const [size, setSize] = useState({ width: null, height: null });

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;

    const measure = () => {
      const containerWidth = element.parentElement?.getBoundingClientRect().width || window.innerWidth;
      const viewportWidth = window.innerWidth || 1024;
      const preferredWidth = isWide
        ? Math.min(760, Math.max(320, viewportWidth * 0.42))
        : viewportWidth;
      const width = Math.max(1, Math.round(Math.min(containerWidth, preferredWidth)));
      const height = Math.round(width * (TABLET_BEZEL_HEIGHT / TABLET_BEZEL_WIDTH));
      setSize((current) => current.width === width && current.height === height ? current : { width, height });
    };

    measure();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    observer?.observe(element.parentElement || element);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [isWide, ref]);

  return size;
}

function SalesPulse({ project, context, summary, loading, freshnessLabel, direction, isWide }) {
  return (
    <aside style={{ ...S.salesPulse, ...(isWide ? S.salesPulseWide : S.salesPulseNarrow) }} aria-label="Nhịp độ bán hàng">
      <div aria-hidden="true" style={S.phoneNotch} />
      <div style={{ ...S.pulseInner, ...(isWide ? null : S.pulseInnerNarrow) }}>
        <div style={S.pulseBrand}><span style={S.pulseLogo}>A</span><span>AbsorpIQ</span><span style={S.pulseLive}><i style={S.pulseLiveDot} />{freshnessLabel.text}</span></div>
        <div style={S.pulseEyebrow}>NHỊP ĐỘ BÁN HÀNG</div>
        <h2 style={S.pulseTitle}>{project?.name || "Tổng quan"}</h2>
        <p style={S.pulseContext}>{context}</p>

        <div style={S.pulseMetrics}>
          <PulseMetric label="Tỷ lệ bán hết" value={loading ? <Skeleton height={24} width="72%" style={S.pulseSkeleton} /> : formatDashboardNumber(summary?.sell_through, { digits: 0, suffix: "%" })} />
          <PulseMetric label="Đã bán" value={loading ? <Skeleton height={24} width="58%" style={S.pulseSkeleton} /> : formatDashboardUnits(summary?.units_sold)} />
          <PulseMetric label="Còn lại" value={loading ? <Skeleton height={24} width="68%" style={S.pulseSkeleton} /> : formatDashboardUnits(summary?.remaining_units)} />
          <PulseMetric label="Vận tốc 30 ngày" value={loading ? <Skeleton height={24} width="68%" style={S.pulseSkeleton} /> : formatDashboardUnits(summary?.velocity_30d, { digits: 1, perWeek: true })} />
          <PulseMetric label="Xu hướng 7/30 ngày" value={direction?.text || "Chưa đủ dữ liệu"} />
        </div>

        <div style={S.pulseReference}>Kỳ tham chiếu: {summary?.earliest_sale_date && summary?.latest_sale_date ? `${formatDashboardDate(summary.earliest_sale_date)} – ${formatDashboardDate(summary.latest_sale_date)}` : "Chưa có dữ liệu"} · {calculatorLabel(summary)}</div>

        <div style={S.pulseNotice}><span style={S.pulseNoticeIcon}><Icon name="warning" size={15} color={color.accent} /></span><span><strong style={S.pulseNoticeTitle}>Dự báo</strong><small style={S.pulseNoticeText}>Chưa khả dụng trong dữ liệu hiện tại</small></span></div>
        <div style={S.pulseNotice}><span style={S.pulseNoticeIcon}><Icon name="rate" size={15} color={color.accent} /></span><span><strong style={S.pulseNoticeTitle}>Tốc độ bán</strong><small style={S.pulseNoticeText}>Chưa đủ dữ liệu để kết luận tốt hay chậm.</small></span></div>
        <p style={S.pulseFoot}>Chọn dự án, phân khu và khoảng thời gian ở màn hình phân tích.</p>
      </div>
    </aside>
  );
}

function PulseMetric({ label, value }) {
  return <div style={S.pulseMetric}><span style={S.pulseMetricLabel}>{label}</span><strong style={S.pulseMetricValue}>{value}</strong></div>;
}

function OverviewEvidence({ summary, summaryLoading, dataQuality, dataQualityLoading, dataQualityError, onDataQualityRetry, areas, areasLoading, areasError, onAreasRetry, isWide }) {
  return <div style={{ ...S.evidenceGrid, ...(isWide ? null : S.evidenceGridNarrow) }}>
    <DataTrustEvidence
      summary={summary}
      summaryLoading={summaryLoading}
      dataQuality={dataQuality}
      loading={dataQualityLoading}
      error={dataQualityError}
      onRetry={onDataQualityRetry}
    />
    <SegmentSignals areas={areas} loading={areasLoading} error={areasError} onRetry={onAreasRetry} />
  </div>;
}

function DataTrustEvidence({ summary, summaryLoading, dataQuality, loading, error, onRetry }) {
  return <section style={S.evidenceCard} aria-label="Độ tin cậy dữ liệu">
    <div style={S.evidenceHead}>
      <div><h2 style={S.panelTitle}>Độ tin cậy dữ liệu</h2><p style={S.panelSub}>Trạng thái và mốc thời gian lấy từ backend, không dùng đồng hồ trình duyệt.</p></div>
      <span style={S.statusBadge}>{summaryLoading || loading ? "Đang tải…" : dataTrustLabel(summary, dataQuality)}</span>
    </div>
    {error ? <div style={S.evidenceBody}><ErrorState error={error} onRetry={onRetry} compact /></div> : summaryLoading || loading ? <div style={S.evidenceBody}><Skeleton height={92} /></div> : <div style={S.evidenceBody}>
      <div style={S.evidenceRows}>
        <EvidenceRow label="Trạng thái dữ liệu" value={dataStatusLabel(summary?.data_status)} />
        <EvidenceRow label="Đồng bộ CRM thành công" value={formatBackendTimestamp(summary?.last_successful_sync)} />
        <EvidenceRow label="Tính toán domain gần nhất" value={formatBackendTimestamp(summary?.updated_at)} />
        <EvidenceRow label="Nguồn / bộ tính" value={calculatorLabel(summary)} />
      </div>
      <p style={S.evidenceNote}>{dataQuality?.error_records ? `Có ${formatDashboardNumber(dataQuality.error_records)} bất thường trong dữ liệu đã tải.` : "Chưa có kết quả đối soát trong luồng Overview hiện tại."}</p>
    </div>}
  </section>;
}

function EvidenceRow({ label, value }) {
  return <div style={S.evidenceRow}><span style={S.evidenceLabel}>{label}</span><strong style={S.evidenceValue}>{value}</strong></div>;
}

function SegmentSignals({ areas, loading, error, onRetry }) {
  const candidates = (areas || []).filter((item) => Number.isFinite(Number(item?.absorption_rate))).sort((a, b) => Number(b.absorption_rate) - Number(a.absorption_rate));
  const highest = candidates[0];
  const lowest = candidates.length > 1 ? candidates[candidates.length - 1] : null;
  const empty = !loading && !error && !highest;

  return <section style={S.evidenceCard} aria-label="Phân khúc và căn hộ">
    <div style={S.evidenceHead}>
      <div><h2 style={S.panelTitle}>Phân khúc & căn hộ</h2><p style={S.panelSub}>Tín hiệu tương đối từ absorption output; chưa phải kết quả xếp hạng.</p></div>
      <Icon name="rate" size={18} color={color.accent} />
    </div>
    <SectionState loading={loading} error={error} empty={empty} onRetry={onRetry} emptyTitle="Chưa có kết quả xếp hạng mới" emptyHint="Danh sách ranking chưa được tải trong luồng Overview hiện tại." compact>
      <div style={S.signalList}>
        <SignalRow label="Hấp thụ cao nhất trong dữ liệu đã tải" item={highest} />
        {lowest && lowest.id !== highest.id && <SignalRow label="Hấp thụ thấp nhất trong dữ liệu đã tải" item={lowest} />}
      </div>
      <p style={S.evidenceNote}>Chưa có kết quả xếp hạng mới; không suy ra điểm ưu tiên, hiệu suất hay rủi ro cho từng căn.</p>
    </SectionState>
  </section>;
}

function SignalRow({ label, item }) {
  return <div style={S.signalRow}><div><strong style={S.evidenceValue}>{item?.name || "Chưa có dữ liệu"}</strong><span style={S.evidenceLabel}>{label}</span></div><strong style={S.signalRate}>{formatDashboardNumber(item?.absorption_rate, { digits: 1, suffix: "%" })}</strong></div>;
}

function OverviewKpis({ summary, loading, error, onRetry, direction, market, marketLoading, marketError }) {
  if (error) return <div style={S.fullCard}><ErrorState error={error} onRetry={onRetry} compact /></div>;
  const cards = [
    { label: "Tỷ lệ hấp thụ", value: formatDashboardNumber(summary?.sell_through, { digits: 1, suffix: "%" }), detail: "So với kế hoạch: Chưa có dữ liệu", badge: "Thực tế", icon: "rate", tone: ACCENT },
    { label: "Hấp thụ ròng", value: formatDashboardUnits(summary?.units_sold), detail: "Kỳ trước: Chưa có dữ liệu", badge: "Đã bán", icon: "sold", tone: color.ok },
    { label: "Vận tốc đặt chỗ", value: formatDashboardUnits(summary?.velocity_30d, { digits: 1, perWeek: true }), detail: direction?.text || "So với kỳ trước: Chưa có dữ liệu", badge: direction?.arrow || "Chưa có dữ liệu", icon: "velocity", tone: NAVY },
    { label: "Tỷ lệ huỷ", value: "Chưa có dữ liệu", detail: "Chưa có trong dữ liệu summary hiện tại", badge: "Chưa có dữ liệu", icon: "warning", tone: color.muted },
    { label: "Căn đang sống", value: formatDashboardUnits(market?.project?.live_units), detail: "Từ market dashboard theo dự án", badge: marketError ? "Không tải được" : "Live units", icon: "remaining", tone: color.ok, loading: marketLoading },
    { label: "Tổng căn hoạt động", value: formatDashboardUnits(market?.metrics?.active_total), detail: "Căn hoạt động trong snapshot", badge: marketError ? "Không tải được" : "Active total", icon: "remaining", tone: ACCENT, loading: marketLoading },
  ];

  return (
    <section aria-label="Tóm tắt KPI" style={S.kpiGrid}>
      {cards.map((card, index) => (
        <article key={card.label} style={S.kpiCard} data-testid={`overview-kpi-${index}`}>
          <div style={S.kpiHead}>
            <span style={{ ...S.kpiIcon, color: card.tone, background: color.canvas }}><Icon name={card.icon} size={16} color={card.tone} /></span>
            <span style={S.kpiLabel}>{card.label}</span>
          </div>
          <div style={S.kpiValue}>{loading || card.loading ? <Skeleton height={28} width="78%" /> : card.value}</div>
          <div style={S.kpiDetail}>{loading || card.loading ? <Skeleton height={14} width="90%" /> : card.detail}</div>
          <span style={{ ...S.kpiBadge, color: card.tone }}>{card.badge}</span>
        </article>
      ))}
    </section>
  );
}

function OverviewTrendChart({ series, metric, onMetric, range, onRange, loading, error, onRetry, dataStatus, emptyMessage }) {
  const [hovered, setHovered] = useState(false);
  const isForecast = metric === "forecast";
  const hasForecast = (series || []).some((row) => Number.isFinite(Number(row?.forecast_value ?? row?.forecast ?? row?.absorption_projection)));
  const chartData = useMemo(() => (series || []).map((row) => ({
    ...row,
    value: metric === "sold" ? numberOrNull(row.units_sold) : metric === "velocity" ? numberOrNull(row.moving_average_30d) : numberOrNull(row.forecast_value ?? row.forecast ?? row.absorption_projection),
    forecast_upper: numberOrNull(row.forecast_upper ?? row.upper_bound ?? row.upper),
    forecast_lower: numberOrNull(row.forecast_lower ?? row.lower_bound ?? row.lower),
  })), [series, metric]);
  const empty = !loading && !error && (chartData.length === 0 || (isForecast && !hasForecast));

  return (
    <section style={S.panel} aria-label="Xu hướng hấp thụ">
      <div style={S.panelHead}>
        <div><h2 style={S.panelTitle}>Hấp thụ — thực tế và dự báo</h2><p style={S.panelSub}>Dữ liệu thực tế lấy từ absorption_daily · dự báo chỉ hiển thị khi backend cung cấp dữ liệu thật.</p></div>
        <div style={S.metricTabs} role="tablist" aria-label="Chỉ số biểu đồ">
          {[['velocity', 'Vận tốc'], ['sold', 'Đã bán'], ['forecast', 'Dự báo']].map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={metric === key} onClick={() => onMetric(key)} style={{ ...S.metricTab, ...(metric === key ? S.metricTabActive : null) }}>{label}</button>
          ))}
        </div>
      </div>
      <div style={S.rangeTabs} aria-label="Khoảng thời gian biểu đồ">
        {[['7d', '7D'], ['30d', '30D'], ['90d', '90D'], ['currentYear', 'YTD'], ['all', 'All']].map(([key, label]) => (
          <button key={key} type="button" aria-pressed={range === key} onClick={() => onRange(key)} style={{ ...S.rangeTab, ...(range === key ? S.rangeTabActive : null) }}>{label}</button>
        ))}
      </div>
      <SectionState
        loading={loading}
        error={error}
        empty={empty}
        onRetry={onRetry}
        emptyTitle={isForecast ? "Chưa có dữ liệu dự báo" : (emptyMessage || (dataStatus === "no_data" ? "Không có dữ liệu trong khoảng đã chọn" : "Chưa có dữ liệu"))}
        emptyHint={isForecast ? "Giai đoạn 10 / Prophet chưa cung cấp chuỗi dự báo cho phạm vi này." : undefined}
        skeleton={<div style={{ padding: space(3) }}><Skeleton height={300} /></div>}
      >
        <div style={{ height: 310 }} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 12, right: 12, left: -12, bottom: 0 }}>
              <CartesianGrid stroke={color.border} vertical={false} />
              <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: size.tiny, fill: color.muted }} axisLine={false} tickLine={false} minTickGap={28} />
              <YAxis tick={{ fontSize: size.tiny, fill: color.muted }} axisLine={false} tickLine={false} width={42} />
              <Tooltip content={<OverviewTooltip metric={metric} />} cursor={{ stroke: color.borderStrong }} />
              {!isForecast && <Area type="monotone" dataKey="value" stroke={NAVY} strokeWidth={2} fill={color.canvas} fillOpacity={0.8} dot={false} />}
              {isForecast && <Area type="monotone" dataKey="forecast_upper" stroke="none" fill={ACCENT} fillOpacity={0.12} />}
              {isForecast && <Line type="monotone" dataKey="value" stroke={ACCENT} strokeWidth={2} strokeDasharray="6 5" dot={false} />}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </SectionState>
      <div style={S.chartLegend}><span><i style={{ ...S.legendLine, background: NAVY }} />Thực tế</span><span><i style={{ ...S.legendLine, background: color.borderStrong }} />Kế hoạch: Chưa có dữ liệu</span><span><i style={{ ...S.legendLine, background: ACCENT }} />Dự báo: Chưa khả dụng</span></div>
      <div style={S.chartFooter}><span>{hovered ? "Di chuyển trên biểu đồ để xem chi tiết" : "Các mốc quan sát lấy từ absorption_daily"}</span><span>{isForecast ? "Khoảng tin cậy: Chưa có dữ liệu" : "Nguồn: dữ liệu nghiệp vụ / hệ thống cũ"}</span></div>
    </section>
  );
}

function OverviewTooltip({ active, payload, label, metric }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  const valueLabel = metric === "sold" ? "Đã bán" : metric === "forecast" ? "Dự báo" : "Vận tốc hấp thụ";
  return <div style={S.tooltip}><div style={S.tooltipDate}>{formatDashboardDate(row.date || label)}</div><div style={S.tooltipRow}><span>{valueLabel}</span><strong>{metric === "sold" ? formatDashboardUnits(row.value) : formatDashboardUnits(row.value, { digits: 1, perWeek: true })}</strong></div>{metric === "forecast" && <div style={S.tooltipRow}><span>Khoảng tin cậy</span><strong>{row.forecast_lower == null || row.forecast_upper == null ? "Chưa có dữ liệu" : `${row.forecast_lower} – ${row.forecast_upper}`}</strong></div>}<div style={S.tooltipSource}>Nguồn: {row.data_source || "dữ liệu nghiệp vụ / hệ thống cũ"}</div></div>;
}

function PriorityAdvisory({ loading, error, onRetry }) {
  const empty = !loading && !error;
  return <section style={S.panel} aria-label="Hành động ưu tiên"><div style={S.panelHead}><div><h2 style={S.panelTitle}>Hành động ưu tiên</h2><p style={S.panelSub}>Chỉ hiển thị đề xuất có trạng thái từ backend; không tự động tạo hoặc phê duyệt.</p></div></div><SectionState loading={loading} error={error} empty={empty} onRetry={onRetry} emptyTitle="Chưa có đề xuất hành động đã được tạo." emptyHint="Luồng Overview hiện chưa tải recommendations nên chưa thể hiển thị ưu tiên bán hàng." compact /></section>;
}

function OverviewBreakdown({ areas, areasLoading, areasError, onAreasRetry, dataQuality, dataQualityLoading, dataQualityError, onDataQualityRetry, project }) {
  const [tab, setTab] = useState("areas");
  return <section style={S.panel} aria-label="Chi tiết vận hành"><div style={S.panelHead}><div><h2 style={S.panelTitle}>Chi tiết & hoạt động</h2><p style={S.panelSub}>{project?.name || "Dự án"} · dữ liệu từ các bảng projection hiện có</p></div><div style={S.breakdownTabs}>{[["areas", "Hiệu suất phân khu"], ["events", "Sự kiện quy trình dữ liệu"]].map(([key, label]) => <button key={key} type="button" onClick={() => setTab(key)} style={{ ...S.metricTab, ...(tab === key ? S.metricTabActive : null) }}>{label}</button>)}</div></div>{tab === "areas" ? <AreaTable areas={areas} loading={areasLoading} error={areasError} onRetry={onAreasRetry} /> : <PipelineEvents dataQuality={dataQuality} loading={dataQualityLoading} error={dataQualityError} onRetry={onDataQualityRetry} />}</section>;
}

function AreaTable({ areas, loading, error, onRetry }) {
  const empty = !loading && !error && !(areas || []).length;
  return <SectionState loading={loading} error={error} empty={empty} onRetry={onRetry} emptyTitle="Chưa có dữ liệu phân khu" compact><div style={S.tableWrap}><table style={S.table}><caption style={S.srOnly}>Hiệu suất các phân khu trong phạm vi hiện tại</caption><thead><tr><th>Phân khu</th><th>Hấp thụ</th><th>Chênh lệch kế hoạch</th><th>Vận tốc</th><th>Tỷ lệ huỷ</th><th>Tồn kho</th><th>Độ tin cậy</th><th>Xu hướng 12 tuần</th></tr></thead><tbody>{(areas || []).map((item) => <tr key={item.id}><td><strong>{item.name || "Chưa có dữ liệu"}</strong><small style={S.rowMeta}>Phân khu · dữ liệu nghiệp vụ</small></td><td>{formatDashboardNumber(item.absorption_rate, { digits: 1, suffix: "%" })}</td><td>Chưa có dữ liệu</td><td>Chưa có dữ liệu</td><td>Chưa có dữ liệu</td><td>{formatDashboardUnits(item.remaining)}</td><td>Chưa có dữ liệu</td><td><span style={S.statusBadge}>Chưa có dữ liệu</span></td></tr>)}</tbody></table></div></SectionState>;
}

function PipelineEvents({ dataQuality, loading, error, onRetry }) {
  const empty = !loading && !error && !dataQuality?.latest_data;
  const status = dataQuality?.status === "error" ? "Có lỗi" : dataQuality?.status === "ok_with_warnings" ? "Có cảnh báo" : "Hoàn tất";
  return <SectionState loading={loading} error={error} empty={empty} onRetry={onRetry} emptyTitle="Chưa có dữ liệu sự kiện quy trình dữ liệu" emptyHint="Lịch sử từng lần chạy chưa có trong endpoint hiện tại." compact>{<div style={S.tableWrap}><table style={S.table}><thead><tr><th>Thời điểm</th><th>Nguồn</th><th>Trạng thái</th><th>Dòng hợp lệ</th><th>Cảnh báo</th></tr></thead><tbody><tr><td>{formatBackendTimestamp(dataQuality.latest_data)}</td><td>{dataQuality.source || "Chưa có dữ liệu"}</td><td><span style={S.statusBadge}>{status}</span></td><td>Chưa có dữ liệu</td><td>{dataQuality.error_records == null ? "Chưa có dữ liệu" : formatDashboardNumber(dataQuality.error_records)}</td></tr></tbody></table></div>}</SectionState>;
}

function UnavailableAnalytics() {
  return <div style={S.analyticsGrid}><UnavailablePanel title="Đóng góp theo phân khúc" hint="Chưa có dữ liệu phân khúc hỗ trợ biểu đồ đóng góp." /><UnavailablePanel title="So với thị trường tham chiếu" hint="Chưa có dữ liệu benchmark thị trường hoặc biến động giá." /></div>;
}

function UnavailablePanel({ title, hint }) {
  return <section style={S.analyticsPanel} aria-label={title}><h2 style={S.panelTitle}>{title}</h2><p style={S.panelSub}>{hint}</p><div style={S.unavailable}>Chưa có dữ liệu</div></section>;
}

function numberOrNull(value) { const n = Number(value); return Number.isFinite(n) ? n : null; }
function shortDate(value) { return formatDashboardDate(value, { day: "2-digit", month: "2-digit" }); }
function dataStatusLabel(status) {
  return {
    ready: "Đã có dữ liệu",
    no_data: "Chưa có dữ liệu",
    no_units: "Chưa có căn hộ",
    insufficient_data: "Chưa đủ dữ liệu",
  }[status] || "Chưa có dữ liệu";
}
function calculatorLabel(summary) {
  return summary?.calculator === "domain_units_deals" || summary?.data_source === "domain_units_deals"
    ? "Căn hộ / giao dịch"
    : summary?.calculator === "legacy_aggregate" || summary?.data_source === "legacy_aggregate"
      ? "Dữ liệu tổng hợp"
      : "Chưa có dữ liệu";
}
function dataTrustLabel(summary, dataQuality) {
  if (dataQuality?.status === "error") return "Có lỗi";
  if (dataQuality?.status === "ok_with_warnings") return "Có cảnh báo";
  const freshness = classifyFreshness(summary);
  return FRESHNESS_LABEL[freshness]?.text || dataStatusLabel(summary?.data_status);
}
function freshnessTone(tone) { return tone === "danger" ? { color: color.danger, background: color.dangerSoft } : tone === "warn" ? { color: color.warn, background: color.warnSoft } : tone === "ok" ? { color: color.ok, background: color.okSoft } : { color: color.muted, background: color.canvas }; }

const S = {
  page: { minWidth: 0 },
  previewPage: { width: "100%", minWidth: 0 },
  deviceComposition: { display: "grid", gap: space(5), alignItems: "stretch", marginBottom: space(4) },
  deviceCompositionWide: { gridTemplateColumns: "minmax(260px, .5fr) minmax(0, 1fr)", gridTemplateRows: "minmax(0, 1fr)", minHeight: 0, alignItems: "end" },
  deviceCompositionNarrow: { gridTemplateColumns: "1fr" },
  tabletPreviewCell: { minWidth: 0, minHeight: 0, width: "100%", display: "flex", justifyContent: "flex-end", alignItems: "flex-end", boxSizing: "border-box" },
  tabletPreviewCellNarrow: { justifyContent: "center" },
  tabletMockup: { display: "block", maxWidth: "100%", minWidth: 0, minHeight: 0, flex: "0 0 auto", flexShrink: 0, position: "relative", overflow: "hidden", boxSizing: "border-box" },
  salesPulse: { position: "relative", width: "100%", maxWidth: 390, justifySelf: "center", aspectRatio: "9 / 19.5", minWidth: 0, minHeight: 0, flexShrink: 0, boxSizing: "border-box", overflow: "hidden", border: "5px solid #050A12", borderRadius: 36, background: "#0B1220", color: color.ink, boxShadow: "inset 1px 1px 0 rgba(255,255,255,.10), inset -1px -1px 0 rgba(0,0,0,.42), 0 24px 48px rgba(32,28,24,.18), 8px 16px 12px -10px rgba(32,28,24,.34)" },
  salesPulseWide: { position: "sticky", top: space(4), alignSelf: "end" },
  salesPulseNarrow: { aspectRatio: "auto", height: 420, minHeight: 0, borderWidth: 4, borderRadius: 28, boxShadow: "inset 1px 1px 0 rgba(255,255,255,.07), 0 12px 26px rgba(32,28,24,.20)" },
  phoneNotch: { position: "absolute", top: 12, left: "50%", width: 78, height: 18, borderRadius: radius.pill, background: "#050A12", transform: "translateX(-50%)", boxShadow: "0 1px 0 rgba(255,255,255,.1)", zIndex: 1 },
  pulseInner: { width: "100%", height: "100%", minWidth: 0, minHeight: 0, flex: "1 1 auto", display: "flex", flexDirection: "column", padding: `${space(12)}px ${space(5)}px ${space(5)}px`, boxSizing: "border-box", borderRadius: 29, overflowX: "auto", overflowY: "auto", overscrollBehavior: "contain", background: "#FFFFFF", color: color.ink, boxShadow: "inset 0 0 0 1px rgba(15,23,42,.08)" },
  pulseInnerNarrow: { height: "100%", minHeight: 0, borderRadius: 22 },
  pulseBrand: { display: "flex", alignItems: "center", gap: space(2), color: color.ink, fontSize: 13, fontWeight: 800, letterSpacing: "-.01em" },
  pulseLogo: { width: 26, height: 26, display: "grid", placeItems: "center", borderRadius: 8, background: color.accent, color: "#111", fontFamily: font.display, fontWeight: 900 },
  pulseLive: { marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 5, color: color.muted, fontSize: 10, fontWeight: 700 },
  pulseLiveDot: { width: 5, height: 5, borderRadius: "50%", background: color.ok },
  pulseEyebrow: { marginTop: space(10), color: color.muted, fontSize: 10, fontWeight: 800, letterSpacing: ".16em" },
  pulseTitle: { margin: `${space(2)}px 0 0`, color: color.ink, fontFamily: font.display, fontSize: 24, lineHeight: 1.12, letterSpacing: "-.04em" },
  pulseContext: { margin: `${space(2)}px 0 0`, color: color.body, fontSize: size.tiny, lineHeight: 1.5 },
  pulseMetrics: { display: "grid", gap: space(2), marginTop: space(6) },
  pulseMetric: { padding: `${space(3)}px 0`, borderTop: `1px solid ${color.border}` },
  pulseMetricLabel: { display: "block", color: color.muted, fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".06em" },
  pulseMetricValue: { display: "block", marginTop: 5, color: color.ink, fontFamily: font.display, fontSize: 22, lineHeight: 1.15 },
  pulseNotice: { display: "flex", alignItems: "center", gap: space(2), marginTop: space(4), padding: space(3), border: `1px solid ${color.border}`, borderRadius: radius.sm, background: color.canvas },
  pulseNoticeIcon: { width: 28, height: 28, display: "grid", placeItems: "center", flex: "0 0 auto", borderRadius: 8, background: "rgba(233,200,62,.12)" },
  pulseNoticeTitle: { display: "block", color: color.ink, fontSize: 11 },
  pulseNoticeText: { display: "block", marginTop: 3, color: color.muted, fontSize: 10, lineHeight: 1.35 },
  pulseReference: { marginTop: space(4), color: color.muted, fontSize: 10, lineHeight: 1.45 },
  pulseFoot: { marginTop: "auto", paddingTop: space(5), color: color.muted, fontSize: 10, lineHeight: 1.5 },
  pulseSkeleton: { background: `linear-gradient(90deg, ${color.canvas} 25%, ${color.border} 37%, ${color.canvas} 63%)`, backgroundSize: "400% 100%" },
  workspaceContent: { minWidth: 0, minHeight: 0 },
  previewWorkspaceContent: { padding: space(3), background: "#F8F8F5", boxSizing: "border-box" },
  pageHeader: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  eyebrow: { color: color.muted, fontSize: 11, fontWeight: 800, letterSpacing: ".12em", marginBottom: space(2) },
  title: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: 32, letterSpacing: "-.04em" },
  subtitle: { margin: `${space(2)}px 0 0`, color: color.muted, fontSize: size.small },
  headerActions: { display: "flex", alignItems: "center", gap: space(2), flexWrap: "wrap" },
  search: { display: "flex", alignItems: "center", gap: space(2), width: 190, padding: "9px 12px", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.sm },
  searchInput: {},
  freshness: { display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 10px", borderRadius: radius.pill, fontSize: size.tiny, fontWeight: 700, whiteSpace: "nowrap" },
  freshnessDot: { width: 6, height: 6, borderRadius: "50%", background: "currentColor" },
  iconButton: { width: 34, height: 34, display: "grid", placeItems: "center", border: `1px solid ${color.border}`, borderRadius: radius.sm, background: color.surface, cursor: "pointer" },
  profile: { width: 34, height: 34, display: "grid", placeItems: "center", borderRadius: "50%", background: color.ink, color: color.surface, fontSize: size.tiny, fontWeight: 800 },
  refreshButton: { display: "inline-flex", alignItems: "center", gap: 6, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "8px 11px", background: color.surface, color: color.body, fontFamily: "inherit", fontWeight: 700, cursor: "pointer" },
  kpiGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: space(3), marginBottom: space(5) },
  kpiCard: { minWidth: 0, minHeight: 148, padding: space(4), background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.sm, boxShadow: shadow },
  kpiHead: { display: "flex", alignItems: "center", gap: space(2) },
  kpiIcon: { width: 30, height: 30, display: "grid", placeItems: "center", borderRadius: radius.sm },
  kpiLabel: { color: color.muted, fontSize: size.tiny, fontWeight: 800, textTransform: "uppercase", letterSpacing: ".05em" },
  kpiValue: { marginTop: space(3), minHeight: 29, color: color.ink, fontFamily: font.display, fontSize: 23, fontWeight: 800, lineHeight: 1.15, fontVariantNumeric: "tabular-nums" },
  kpiDetail: { minHeight: 32, marginTop: space(1), color: color.muted, fontSize: size.tiny, lineHeight: 1.45 },
  kpiBadge: { display: "inline-block", marginTop: space(1), fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: ".06em" },
  fullCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, marginBottom: space(5) },
  mainGrid: { display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(280px, 1fr)", gap: space(4), alignItems: "stretch", marginBottom: space(4) },
  mainGridNarrow: { gridTemplateColumns: "1fr" },
  panel: { minWidth: 0, background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.sm, boxShadow: shadow, overflow: "hidden" },
  panelHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(3), padding: `${space(4)}px ${space(4)}px ${space(3)}px`, flexWrap: "wrap" },
  panelTitle: { margin: 0, color: color.ink, fontSize: 16, fontWeight: 800 },
  panelSub: { margin: "4px 0 0", color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  metricTabs: { display: "flex", gap: 3, padding: 3, background: color.canvas, borderRadius: radius.sm },
  metricTab: { border: "none", borderRadius: 6, padding: "7px 10px", color: color.muted, background: "transparent", fontFamily: "inherit", fontSize: size.tiny, fontWeight: 700, cursor: "pointer" },
  metricTabActive: { color: color.accent, background: color.surface, boxShadow: shadow },
  evidenceGrid: { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: space(4), marginBottom: space(4) },
  evidenceGridNarrow: { gridTemplateColumns: "1fr" },
  evidenceCard: { minWidth: 0, background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.sm, boxShadow: shadow, overflow: "hidden" },
  evidenceHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(3), padding: `${space(4)}px ${space(4)}px ${space(3)}px` },
  evidenceBody: { padding: `0 ${space(4)}px ${space(4)}px` },
  evidenceRows: { display: "grid", gap: space(2), paddingTop: space(2), borderTop: `1px solid ${color.border}` },
  evidenceRow: { display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: space(3), paddingTop: space(1) },
  evidenceLabel: { display: "block", color: color.muted, fontSize: 10, lineHeight: 1.4 },
  evidenceValue: { color: color.body, fontSize: 12, lineHeight: 1.4 },
  evidenceNote: { margin: `${space(3)}px 0 0`, color: color.muted, fontSize: 10, lineHeight: 1.45 },
  signalList: { display: "grid", gap: space(2), padding: `0 ${space(4)}px ${space(2)}px` },
  signalRow: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: space(3), padding: `${space(3)}px 0`, borderTop: `1px solid ${color.border}` },
  signalRate: { color: color.accent, fontFamily: font.display, fontSize: 18 },
  rangeTabs: { display: "flex", gap: space(1), padding: `0 ${space(5)}px ${space(3)}px`, borderBottom: `1px solid ${color.border}` },
  rangeTab: { border: "none", borderRadius: radius.pill, padding: "5px 10px", background: "transparent", color: color.muted, fontFamily: "inherit", fontSize: 11, fontWeight: 700, cursor: "pointer" },
  rangeTabActive: { color: color.accent, background: color.accentSoft },
  chartLegend: { display: "flex", justifyContent: "flex-end", gap: space(3), padding: `0 ${space(4)}px ${space(2)}px`, color: color.muted, fontSize: 10, flexWrap: "wrap" },
  legendLine: { display: "inline-block", width: 14, height: 2, marginRight: 5, verticalAlign: "middle" },
  chartFooter: { display: "flex", justifyContent: "space-between", gap: space(3), padding: `${space(2)}px ${space(5)}px ${space(4)}px`, color: color.muted, fontSize: 11, flexWrap: "wrap" },
  tooltip: { minWidth: 190, padding: space(3), border: `1px solid ${color.border}`, borderRadius: radius.sm, background: color.surface, boxShadow: shadow, fontSize: size.tiny },
  tooltipDate: { color: color.muted, marginBottom: space(2), fontWeight: 700 },
  tooltipRow: { display: "flex", justifyContent: "space-between", gap: space(3), padding: "3px 0", color: color.body },
  tooltipSource: { marginTop: space(2), color: color.muted, fontSize: 10 },
  breakdownTabs: { display: "flex", gap: 3, padding: 3, background: color.canvas, borderRadius: radius.sm },
  tableWrap: { overflowX: "auto" },
  table: { width: "100%", minWidth: 820, borderCollapse: "collapse", fontSize: 11.5 },
  statusBadge: { display: "inline-block", padding: "3px 7px", borderRadius: radius.pill, background: color.canvas, color: color.muted, fontSize: 10, fontWeight: 700, whiteSpace: "nowrap" },
  rowMeta: { display: "block", marginTop: 3, color: color.muted, fontSize: 10 },
  analyticsGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: space(4), marginTop: space(4) },
  analyticsPanel: { minHeight: 150, padding: space(4), background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.sm, boxShadow: shadow },
  unavailable: { display: "grid", placeItems: "center", minHeight: 75, marginTop: space(3), borderTop: `1px solid ${color.border}`, color: color.muted, fontSize: 12, fontWeight: 700 },
  disclaimer: { marginTop: space(4), color: color.muted, fontSize: 11, lineHeight: 1.5 },
  srOnly: { position: "absolute", width: 1, height: 1, padding: 0, margin: -1, overflow: "hidden", clip: "rect(0,0,0,0)", whiteSpace: "nowrap", border: 0 },
};
