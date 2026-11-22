// Datos falsos del modo simulado. El tema «Esencial» es una copia del incluido en
// src/luzeira/recursos/temas/esencial/theme.json (si cambia allí, da igual: es solo
// para desarrollar y hacer capturas).

import type { ThemeJson } from '../model/types.ts'
import type { SensorInfo } from '../api.ts'
import { currentLanguage, setLanguage, t } from '../model/i18n.ts'

/**
 * `t` en otro idioma que el activo (la demo crea sus datos antes de que el editor siga el
 * idioma de `?lang=`). Cambia el idioma solo mientras dura la llamada.
 */
export function inLanguage<T>(lang: string, fn: () => T): T {
  const previous = currentLanguage()
  setLanguage(lang)
  try {
    return fn()
  } finally {
    setLanguage(previous)
  }
}

export const ESENCIAL: ThemeJson = {
  format: 'luzeira-theme/1',
  name: 'Esencial',
  author: 'Luzeira',
  orientation: 'landscape',
  background: { color: '#0c101c' },
  widgets: [
    { type: 'clock', format: 'HH:MM', box: { x: 16, y: 8, width: 200, height: 64 }, style: { size: 52, bold: true, color: '#ff8c00', align: 'left' } },
    { type: 'clock', format: 'date-long', box: { x: 216, y: 14, width: 248, height: 28 }, style: { size: 18, color: '#e6e6e6', align: 'right' } },
    { type: 'clock', format: 'date-short', box: { x: 216, y: 42, width: 248, height: 24 }, style: { size: 15, color: '#8a93a6', align: 'right' } },
    { type: 'text', text: 'CPU', box: { x: 16, y: 86, width: 216, height: 24 }, style: { size: 16, bold: true, color: '#8a93a6', align: 'left' } },
    { type: 'sensor', sensor: 'cpu.usage', box: { x: 16, y: 110, width: 216, height: 52 }, style: { size: 42, bold: true, align: 'left' } },
    { type: 'bar', sensor: 'cpu.usage', box: { x: 16, y: 168, width: 216, height: 10 } },
    { type: 'text', text: 'GPU', box: { x: 248, y: 86, width: 120, height: 24 }, style: { size: 16, bold: true, color: '#8a93a6', align: 'left' } },
    { type: 'sensor', sensor: 'gpu.temperature', box: { x: 368, y: 86, width: 96, height: 24 }, style: { size: 16, color: '#8a93a6', align: 'right' } },
    { type: 'sensor', sensor: 'gpu.usage', box: { x: 248, y: 110, width: 216, height: 52 }, style: { size: 42, bold: true, align: 'left' } },
    { type: 'bar', sensor: 'gpu.usage', box: { x: 248, y: 168, width: 216, height: 10 } },
    { type: 'text', text: 'RAM', box: { x: 16, y: 200, width: 100, height: 24 }, style: { size: 16, bold: true, color: '#8a93a6', align: 'left' } },
    { type: 'sensor', sensor: 'memory.used', decimals: 1, box: { x: 116, y: 200, width: 116, height: 24 }, style: { size: 16, color: '#8a93a6', align: 'right' } },
    { type: 'sensor', sensor: 'memory.usage', box: { x: 16, y: 224, width: 216, height: 52 }, style: { size: 42, bold: true, align: 'left' } },
    { type: 'bar', sensor: 'memory.usage', box: { x: 16, y: 282, width: 216, height: 10 } },
    { type: 'text', text: 'RED', text_i18n: { en: 'NET' }, box: { x: 248, y: 200, width: 216, height: 24 }, style: { size: 16, bold: true, color: '#8a93a6', align: 'left' } },
    { type: 'sensor', sensor: 'network.download', decimals: 1, prefix: '↓ ', box: { x: 248, y: 228, width: 216, height: 30 }, style: { size: 24, bold: true, align: 'left' } },
    { type: 'sensor', sensor: 'network.upload', decimals: 1, prefix: '↑ ', box: { x: 248, y: 260, width: 216, height: 30 }, style: { size: 24, color: '#c8ccd6', align: 'left' } },
  ],
}

/** El tema «del usuario» de la demo, con el nombre en el idioma de la demo. */
export const miTema = (): ThemeJson => ({
  format: 'luzeira-theme/1',
  name: t('demo.mi_tema'),
  author: null,
  orientation: 'landscape',
  background: { color: '#14161c' },
  widgets: [
    { type: 'text', text: 'Mi PC', text_i18n: { en: 'My PC' }, box: { x: 16, y: 16, width: 200, height: 40 }, style: { size: 28, bold: true, color: '#ff8c00', align: 'left' } },
    { type: 'clock', format: 'HH:MM:SS', box: { x: 264, y: 16, width: 200, height: 40 }, style: { size: 28, align: 'right' } },
    { type: 'sensor', sensor: 'gpu.name', box: { x: 16, y: 80, width: 448, height: 32 }, style: { size: 20, color: '#c8ccd6', align: 'left' } },
    { type: 'sensor', sensor: 'cpu.usage', prefix: 'CPU ', box: { x: 16, y: 136, width: 220, height: 48 }, style: { size: 34, bold: true, align: 'left' } },
    { type: 'bar', sensor: 'cpu.usage', box: { x: 16, y: 192, width: 448, height: 16 }, fill: '#ff8c00' },
    { type: 'sensor', sensor: 'gpu.temperature', prefix: 'GPU ', box: { x: 244, y: 136, width: 220, height: 48 }, style: { size: 34, bold: true, align: 'right' } },
  ],
})

// `label`, `fixedText` y `missing` son claves del catálogo `demo` (se traducen al leerlos).
type SimSensor = Omit<SensorInfo, 'value' | 'text' | 'reason'> & { base?: number; spread?: number; fixedText?: string; missing?: string }

export const SIM_SENSORS: SimSensor[] = [
  { id: 'cpu.usage', label: 'demo.sensor.cpu.usage', unit: '%', category: 'cpu', kind: 'number', base: 23, spread: 18 },
  { id: 'cpu.frequency', label: 'demo.sensor.cpu.frequency', unit: 'MHz', category: 'cpu', kind: 'number', base: 4050, spread: 300 },
  { id: 'cpu.temperature', label: 'demo.sensor.cpu.temperature', unit: '°C', category: 'cpu', kind: 'number', missing: 'demo.sensor.cpu.temperature.falta' },
  { id: 'cpu.name', label: 'demo.sensor.cpu.name', unit: '', category: 'cpu', kind: 'text', fixedText: 'demo.sensor.cpu.name.valor' },
  { id: 'memory.usage', label: 'demo.sensor.memory.usage', unit: '%', category: 'memory', kind: 'number', base: 48, spread: 3 },
  { id: 'memory.used', label: 'demo.sensor.memory.used', unit: 'GB', category: 'memory', kind: 'number', base: 15.4, spread: 0.4 },
  { id: 'memory.total', label: 'demo.sensor.memory.total', unit: 'GB', category: 'memory', kind: 'number', base: 31.9, spread: 0 },
  { id: 'network.upload', label: 'demo.sensor.network.upload', unit: 'MB/s', category: 'network', kind: 'number', base: 0.3, spread: 0.3 },
  { id: 'network.download', label: 'demo.sensor.network.download', unit: 'MB/s', category: 'network', kind: 'number', base: 2.1, spread: 2 },
  { id: 'disk.usage.c', label: 'demo.sensor.disk.usage.c', unit: '%', category: 'disk', kind: 'number', base: 61, spread: 0 },
  { id: 'disk.read', label: 'demo.sensor.disk.read', unit: 'MB/s', category: 'disk', kind: 'number', base: 4, spread: 4 },
  { id: 'disk.write', label: 'demo.sensor.disk.write', unit: 'MB/s', category: 'disk', kind: 'number', base: 1.5, spread: 1.5 },
  { id: 'system.uptime', label: 'demo.sensor.system.uptime', unit: 'h', category: 'system', kind: 'number', base: 5.2, spread: 0 },
  { id: 'gpu.usage', label: 'demo.sensor.gpu.usage', unit: '%', category: 'gpu', kind: 'number', base: 35, spread: 25 },
  { id: 'gpu.temperature', label: 'demo.sensor.gpu.temperature', unit: '°C', category: 'gpu', kind: 'number', base: 52, spread: 4 },
  { id: 'gpu.power', label: 'demo.sensor.gpu.power', unit: 'W', category: 'gpu', kind: 'number', base: 110, spread: 40 },
  { id: 'gpu.fan', label: 'demo.sensor.gpu.fan', unit: '%', category: 'gpu', kind: 'number', base: 30, spread: 5 },
  { id: 'gpu.name', label: 'demo.sensor.gpu.name', unit: '', category: 'gpu', kind: 'text', fixedText: 'demo.sensor.gpu.name.valor' },
]

export function readSimSensors(): SensorInfo[] {
  return SIM_SENSORS.map((s) => {
    const { base, spread, fixedText, missing, ...rest } = s
    const info = { ...rest, label: t(rest.label) }
    if (missing) return { ...info, value: null, text: null, reason: t(missing) }
    if (fixedText !== undefined) return { ...info, value: null, text: t(fixedText), reason: null }
    const v = Math.max(0, (base ?? 0) + (Math.random() * 2 - 1) * (spread ?? 0))
    return { ...info, value: Math.round(v * 100) / 100, text: null, reason: null }
  })
}
