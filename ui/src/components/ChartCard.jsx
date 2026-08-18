import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

/** 由后端 chart 结构生成 ECharts option（bar/line/pie）。 */
function buildOption(chart) {
  const { type, x = [], series = [] } = chart;
  const base = { tooltip: {}, legend: {}, grid: { left: 44, right: 20, bottom: 30, top: 40 } };
  if (type === 'pie') {
    const data = (series[0]?.data || []).map((v, i) => ({ name: x[i] ?? '', value: v }));
    return { ...base, tooltip: { trigger: 'item' }, series: [{ type: 'pie', radius: '62%', data }] };
  }
  if (type === 'line') {
    return {
      ...base,
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: x },
      yAxis: { type: 'value' },
      series: series.map((s) => ({ name: s.name, type: 'line', smooth: true, data: s.data })),
    };
  }
  return {
    ...base,
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: x },
    yAxis: { type: 'value' },
    series: series.map((s) => ({ name: s.name, type: 'bar', barMaxWidth: 40, data: s.data })),
  };
}

export default function ChartCard({ chart }) {
  const chartElRef = useRef(null);
  const ecRef = useRef(null);
  useEffect(() => {
    if (!chartElRef.current) return undefined;
    const ec = echarts.init(chartElRef.current);
    ecRef.current = ec;
    ec.setOption(buildOption(chart));
    const onResize = () => ec.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      ec.dispose();
      ecRef.current = null;
    };
  }, [chart]);

  function download() {
    const ec = ecRef.current;
    if (!ec) return;
    const url = ec.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' });
    const a = document.createElement('a');
    a.href = url;
    a.download = `chart-${Date.now()}.png`;
    a.click();
  }

  return (
    <div className="chart-card">
      <div className="chart-head">
        <span className="chart-title">📈 数据图表</span>
        <button className="chart-download" onClick={download} title="下载 PNG">
          ⬇ 下载图片
        </button>
      </div>
      <div className="chart" ref={chartElRef} />
    </div>
  );
}
