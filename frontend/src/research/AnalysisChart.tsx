import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, ToolboxComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { AnalysisResult } from './labTypes'
import { useTheme } from './ThemeProvider'

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, ToolboxComponent, CanvasRenderer])

function chartAppearance() {
  const styles = getComputedStyle(document.documentElement)
  const color = (name: string) => styles.getPropertyValue(`--theme-${name}`).trim()
  const muted = color('fg-muted'), surface = color('surface')
  return {
    color: [color('chart-1'), color('chart-2'), color('chart-3')],
    backgroundColor: 'transparent',
    legend: { textStyle: { color: muted, fontSize: 10 } },
    tooltip: { backgroundColor: surface, borderColor: color('border'), textStyle: { color: color('fg-primary') } },
    xAxis: { nameTextStyle: { color: muted, fontSize: 10 }, axisLabel: { color: muted, fontSize: 9 }, axisLine: { lineStyle: { color: color('border-strong') } } },
    yAxis: { nameTextStyle: { color: muted, fontSize: 10 }, axisLabel: { color: muted, fontSize: 9 }, splitLine: { lineStyle: { color: color('border-soft'), type: 'dashed' as const } } },
    toolbox: { iconStyle: { borderColor: muted }, feature: { saveAsImage: { backgroundColor: surface } } },
  }
}

export default function AnalysisChart({ result }: { result: AnalysisResult }) {
  const element = useRef<HTMLDivElement>(null)
  const instance = useRef<ReturnType<typeof echarts.init> | null>(null)
  const { preferences, resolvedMode } = useTheme()
  useEffect(() => {
    if (!element.current || !result.chart) return
    const chart = echarts.init(element.current)
    instance.current = chart
    const input = result.chart
    const series = input.type === 'series' ? ['真实观测', 'LSTM 单步预测', '持久性基线'].map((name, i) => ({
      name, type: 'line' as const, showSymbol: false, lineStyle: { width: i === 2 ? 1.5 : 2, type: i === 2 ? 'dashed' as const : 'solid' as const },
      data: (input.points as { index: number; actual: number; predicted: number; baseline: number }[]).map(point => [point.index, [point.actual, point.predicted, point.baseline][i]]),
    })) : [{ name: '观测值', type: 'scatter' as const, symbolSize: 7, data: input.points, itemStyle: { opacity: .65 } }]
    if (input.type === 'scatter' && input.slope != null && input.intercept != null) {
      const xs = (input.points as number[][]).map(point => point[0])
      ;(series as unknown[]).push({ name: '拟合直线', type: 'line', showSymbol: false, data: [Math.min(...xs), Math.max(...xs)].map(x => [x, input.slope! * x + input.intercept!]) })
    }
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches || document.querySelector('.workbench')?.getAttribute('data-motion') === 'off'
    chart.setOption({ animationDuration: reduceMotion ? 0 : 900,
      grid: { left: 58, right: 25, top: 54, bottom: 62 }, legend: { top: 6 },
      tooltip: { trigger: input.type === 'series' ? 'axis' : 'item', renderMode: 'richText' },
      xAxis: { type: 'value', name: input.x_label, nameLocation: 'middle', nameGap: 33, splitLine: { show: false } },
      yAxis: { type: 'value', scale: true, name: input.y_label },
      toolbox: { top: 28, right: 0, feature: { saveAsImage: { title: '保存图表', name: result.title, pixelRatio: 2 } } },
      dataZoom: [{ type: 'inside', filterMode: 'none' }], series,
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(element.current)
    return () => { observer.disconnect(); chart.dispose(); instance.current = null }
  }, [result])
  useEffect(() => {
    // Update appearance without recreating the canvas or losing the user's zoom.
    instance.current?.setOption(chartAppearance())
  }, [result, preferences.palette, resolvedMode])
  return <div ref={element} className="analysis-chart" role="img" aria-label={result.chart?.type === 'series' ? '真实序列、LSTM 预测和持久性基线比较图' : '变量散点图'}/>
}
