// Panel derecho: propiedades del elemento seleccionado según su tipo. Sin selección
// muestra las del tema (ThemePanel).

import type { Dispatch } from 'react'
import type { Action } from '../model/editorState.ts'
import { editTranslatable, formatClock, formatReading, localized, translationMode } from '../model/format.ts'
import { clockFormatLabel, widgetTypeInfo } from '../model/theme.ts'
import { LIMITS, screenSize, type Align, type ClockFormat, type Theme, type Translations, type Widget } from '../model/types.ts'
import { fieldOf, type Issue } from '../model/validate.ts'
import { useIdioma, useT } from '../i18n/contexto.ts'
import { ColorField, InfoTip, NumberField, Segmented as SegmentedField, SelectField, TextField, Toggle } from './fields.tsx'
import { FontPicker, ImagePicker } from './ResourcePicker.tsx'
import { SensorPicker } from './SensorPicker.tsx'
import { IconAlignCenter, IconAlignLeft, IconAlignRight, IconCenterH, IconCenterV, IconCopy, IconTrash, WidgetIcon } from './icons.tsx'
import type { Services } from './services.ts'

interface Props {
  theme: Theme
  widget: Widget
  id: string
  issues: Issue[] // ya filtrados a este elemento
  services: Services
  dispatch: Dispatch<Action>
  onDuplicate: () => void
  onRemove: () => void
}

const CLOCK_FORMATS: ClockFormat[] = ['HH:MM', 'HH:MM:SS', 'date-short', 'date-long', 'weekday']

/** Los idiomas de unas traducciones, para el aviso («en, de»); null si no hay. */
function languagesOf(tr: Translations | undefined): string | null {
  const codes = tr ? Object.keys(tr) : []
  return codes.length ? codes.join(', ') : null
}

export function PropertiesPanel({ theme, widget, id, issues, services, dispatch, onDuplicate, onRemove }: Props) {
  const t = useT()
  const { lang } = useIdioma()
  const screen = screenSize(theme)
  const err = (field: string) => issues.find((i) => fieldOf(i.path) === field)?.message ?? null
  const general = issues.filter((i) => fieldOf(i.path) === '')

  // Cada campo funde sus cambios seguidos en un paso de deshacer.
  const update = (next: Widget, field: string) =>
    dispatch({ type: 'updateWidget', id, widget: next, mergeKey: `field:${id}:${field}`, at: Date.now() })
  const setBox = (patch: Partial<Widget['box']>, field: string) =>
    dispatch({ type: 'setBox', id, box: { ...widget.box, ...patch }, mergeKey: `field:${id}:${field}`, at: Date.now() })
  const setStyle = (patch: Partial<Widget['style']>, field: string) =>
    update({ ...widget, style: { ...widget.style, ...patch } } as Widget, `style.${field}`)

  /**
   * Aviso bajo un texto que trae traducciones (C-62): el campo enseña el del idioma activo;
   * si lo tiene, se cambia solo ese, y si no, el original y se quitan las traducciones.
   */
  const translatedNote = (tr: Translations | undefined, key: string) => {
    const langs = languagesOf(tr)
    return langs ? (
      <p className="hint" style={{ marginTop: -6, marginBottom: 10 }}>
        {t(key, { idiomas: langs })}
      </p>
    ) : null
  }
  const textNote = (tr: Translations | undefined) =>
    translatedNote(tr, translationMode(tr, lang) === 'translation' ? 'propiedades.traducido' : 'propiedades.traducido.quitar')

  const info = widgetTypeInfo(widget.type)
  const b = widget.box
  const isTextLike = widget.type === 'text' || widget.type === 'clock' || widget.type === 'sensor'
  const sensorInfo = widget.type === 'sensor' ? services.sensors.find((s) => s.id === widget.sensor) : undefined
  const sensorIsText = sensorInfo?.kind === 'text'

  return (
    <>
      <div className="props-title">
        <WidgetIcon type={widget.type} />
        <div>
          <h2>
            {info.label}
            <InfoTip text={info.help} />
          </h2>
        </div>
      </div>
      <div className="props-actions">
        <button type="button" className="btn small" onClick={onDuplicate} title={t('propiedades.duplicar.ayuda')}>
          <IconCopy /> {t('propiedades.duplicar')}
        </button>
        <button type="button" className="btn small danger" onClick={onRemove} title={t('propiedades.borrar.ayuda')}>
          <IconTrash /> {t('comun.borrar')}
        </button>
      </div>

      <div className="panel-body">
        {general.map((g) => (
          <p key={g.message} className="err" role="alert">
            {g.message}
          </p>
        ))}

        <section className="section">
          <h3>
            {t('propiedades.posicion')}
            <InfoTip text={t('propiedades.posicion.ayuda', { ancho: screen.width, alto: screen.height })} />
          </h3>
          <div className="row">
            <NumberField label={t('propiedades.x')} affix="X" value={b.x} min={0} max={screen.width - b.width} onChange={(x) => setBox({ x }, 'box.x')} error={err('box.x')} />
            <NumberField label={t('propiedades.y')} affix="Y" value={b.y} min={0} max={screen.height - b.height} onChange={(y) => setBox({ y }, 'box.y')} error={err('box.y')} />
            <NumberField label={t('propiedades.ancho')} affix={t('propiedades.ancho.corto')} value={b.width} min={1} max={screen.width - b.x} onChange={(width) => setBox({ width: Math.min(width, screen.width - b.x) }, 'box.width')} error={err('box.width')} />
            <NumberField label={t('propiedades.alto')} affix={t('propiedades.alto.corto')} value={b.height} min={1} max={screen.height - b.y} onChange={(height) => setBox({ height: Math.min(height, screen.height - b.y) }, 'box.height')} error={err('box.height')} />
          </div>
          {/* Errores del recuadro entero («widgets.N.box», p. ej. «se sale de la pantalla»). */}
          {err('box') && (
            <p className="err" role="alert">
              {err('box')}
            </p>
          )}
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" className="btn small" onClick={() => setBox({ x: Math.round((screen.width - b.width) / 2) }, 'center')} title={t('propiedades.centrar_h')}>
              <IconCenterH /> {t('propiedades.centrar')}
            </button>
            <button type="button" className="btn small" onClick={() => setBox({ y: Math.round((screen.height - b.height) / 2) }, 'center')} title={t('propiedades.centrar_v')}>
              <IconCenterV /> {t('propiedades.centrar')}
            </button>
          </div>
        </section>

        {widget.type === 'text' && (
          <section className="section">
            <h3>{t('propiedades.contenido')}</h3>
            <TextField
              label={t('propiedades.texto')}
              multiline
              value={localized(widget.text, widget.text_i18n, lang)}
              maxLength={LIMITS.textMax}
              onChange={(text) => update(editTranslatable(widget, 'text', text, lang), 'text')}
              hint={t('propiedades.texto.ayuda')}
              error={err('text') ?? err('text_i18n')}
            />
            {textNote(widget.text_i18n)}
          </section>
        )}

        {widget.type === 'clock' && (
          <section className="section">
            <h3>{t('propiedades.contenido')}</h3>
            <SelectField
              label={t('propiedades.reloj')}
              value={widget.format}
              options={CLOCK_FORMATS.map((f) => ({ value: f, label: `${clockFormatLabel(f)} — ${formatClock(f, new Date())}` }))}
              onChange={(format) => update({ ...widget, format }, 'format')}
              hint={t('propiedades.reloj.ayuda')}
            />
          </section>
        )}

        {widget.type === 'sensor' && (
          <section className="section">
            <h3>{t('propiedades.dato')}</h3>
            <SensorPicker
              label={t('propiedades.sensor')}
              hint={t('propiedades.sensor.ayuda')}
              value={widget.sensor}
              sensors={services.sensors}
              kinds={['number', 'text']}
              onChange={(sensor) => update({ ...widget, sensor }, 'sensor')}
              error={err('sensor')}
            />
            <p className="hint" style={{ marginTop: -4, marginBottom: 10 }}>
              {t('propiedades.se_vera')} <strong className="live">{formatReading(widget, sensorInfo)}</strong>
            </p>
            {!sensorIsText && (
              <div className="row">
                <NumberField label={t('propiedades.decimales')} value={widget.decimals} min={LIMITS.decimalsMin} max={LIMITS.decimalsMax} onChange={(decimals) => update({ ...widget, decimals }, 'decimals')} hint={t('propiedades.decimales.ayuda')} error={err('decimals')} />
                <div className="field">
                  <span className="label">{t('propiedades.unidad')}</span>
                  <Toggle label={sensorInfo?.unit ? t('propiedades.unidad.mostrar_cual', { unidad: sensorInfo.unit }) : t('propiedades.unidad.mostrar')} checked={widget.show_unit} onChange={(show_unit) => update({ ...widget, show_unit }, 'show_unit')} />
                </div>
              </div>
            )}
            {sensorIsText && (
              <NumberField label={t('propiedades.largo')} value={widget.max_chars} min={LIMITS.maxCharsMin} max={LIMITS.maxCharsMax} onChange={(max_chars) => update({ ...widget, max_chars }, 'max_chars')} hint={t('propiedades.largo.ayuda')} error={err('max_chars')} />
            )}
            <div className="row">
              <TextField label={t('propiedades.antes')} value={localized(widget.prefix, widget.prefix_i18n, lang)} maxLength={LIMITS.shortTextMax} onChange={(prefix) => update(editTranslatable(widget, 'prefix', prefix, lang), 'prefix')} placeholder="CPU " hint={t('propiedades.antes.ayuda')} error={err('prefix') ?? err('prefix_i18n')} />
              <TextField label={t('propiedades.despues')} value={localized(widget.suffix, widget.suffix_i18n, lang)} maxLength={LIMITS.shortTextMax} onChange={(suffix) => update(editTranslatable(widget, 'suffix', suffix, lang), 'suffix')} placeholder="" hint={t('propiedades.despues.ayuda')} error={err('suffix') ?? err('suffix_i18n')} />
            </div>
            {translatedNote(widget.prefix_i18n || widget.suffix_i18n ? { ...widget.prefix_i18n, ...widget.suffix_i18n } : undefined, 'propiedades.traducido.sensor')}
            <NumberField label={t('propiedades.intervalo')} integer={false} step={0.5} value={widget.interval} min={LIMITS.intervalMin} max={LIMITS.intervalMax} onChange={(interval) => update({ ...widget, interval }, 'interval')} hint={t('propiedades.intervalo.ayuda')} error={err('interval')} />
          </section>
        )}

        {widget.type === 'bar' && (
          <section className="section">
            <h3>{t('propiedades.dato_escala')}</h3>
            <SensorPicker
              label={t('propiedades.sensor')}
              hint={t('propiedades.barra.sensor.ayuda')}
              value={widget.sensor}
              sensors={services.sensors}
              kinds={['number']}
              onChange={(sensor) => update({ ...widget, sensor }, 'sensor')}
              error={err('sensor')}
            />
            <div className="row">
              <NumberField label={t('propiedades.barra.min')} integer={false} value={widget.min} onChange={(min) => update({ ...widget, min }, 'min')} error={err('min')} hint={t('propiedades.barra.min.ayuda')} />
              <NumberField label={t('propiedades.barra.max')} integer={false} value={widget.max} onChange={(max) => update({ ...widget, max }, 'max')} error={err('max')} hint={t('propiedades.barra.max.ayuda')} />
            </div>
            <SegmentedField
              label={t('propiedades.barra.direccion')}
              value={widget.direction}
              options={[
                { value: 'horizontal', label: t('propiedades.barra.horizontal'), help: t('propiedades.barra.horizontal.ayuda') },
                { value: 'vertical', label: t('propiedades.barra.vertical'), help: t('propiedades.barra.vertical.ayuda') },
              ]}
              onChange={(direction) => update({ ...widget, direction }, 'direction')}
            />
            <div className="row">
              <ColorField label={t('propiedades.barra.relleno')} value={widget.fill} onChange={(fill) => update({ ...widget, fill }, 'fill')} error={err('fill')} />
              <ColorField label={t('propiedades.barra.fondo')} value={widget.track} onChange={(track) => update({ ...widget, track }, 'track')} error={err('track')} />
            </div>
            <NumberField label={t('propiedades.intervalo')} integer={false} step={0.5} value={widget.interval} min={LIMITS.intervalMin} max={LIMITS.intervalMax} onChange={(interval) => update({ ...widget, interval }, 'interval')} error={err('interval')} />
          </section>
        )}

        {widget.type === 'image' && (
          <section className="section">
            <h3>{t('propiedades.imagen')}</h3>
            <ImagePicker
              label={t('propiedades.imagen')}
              hint={t('propiedades.imagen.ayuda')}
              value={widget.file}
              services={services}
              onChange={(file) => file && update({ ...widget, file }, 'file')}
              error={err('file')}
            />
          </section>
        )}

        {isTextLike && (
          <section className="section">
            <h3>{t('propiedades.letra')}</h3>
            <div className="row">
              <NumberField label={t('propiedades.tamano')} value={widget.style.size} min={LIMITS.sizeMin} max={LIMITS.sizeMax} onChange={(size) => setStyle({ size }, 'size')} hint={t('propiedades.tamano.ayuda', { min: LIMITS.sizeMin, max: LIMITS.sizeMax })} error={err('style.size')} />
              <div className="field">
                <span className="label">{t('propiedades.grosor')}</span>
                <Toggle label={t('propiedades.negrita')} checked={widget.style.bold} onChange={(bold) => setStyle({ bold }, 'bold')} />
              </div>
            </div>
            <ColorField label={t('propiedades.color')} value={widget.style.color} onChange={(color) => setStyle({ color }, 'color')} error={err('style.color')} />
            <SegmentedField<Align>
              label={t('propiedades.alineacion')}
              value={widget.style.align}
              options={[
                { value: 'left', label: t('propiedades.izquierda'), icon: <IconAlignLeft /> },
                { value: 'center', label: t('propiedades.centro'), icon: <IconAlignCenter /> },
                { value: 'right', label: t('propiedades.derecha'), icon: <IconAlignRight /> },
              ]}
              onChange={(align) => setStyle({ align }, 'align')}
              hint={t('propiedades.alineacion.ayuda')}
            />
            <FontPicker value={widget.style.font} services={services} onChange={(font) => setStyle({ font }, 'font')} error={err('style.font')} />
          </section>
        )}

        <section className="section">
          <h3>{t('propiedades.fondo')}</h3>
          <Toggle
            label={t('propiedades.fondo.propio')}
            checked={widget.style.background !== null}
            onChange={(on) => setStyle({ background: on ? '#1d2027' : null }, 'background')}
            hint={t('propiedades.fondo.ayuda')}
          />
          {widget.style.background !== null && (
            <ColorField label={t('propiedades.fondo.color')} value={widget.style.background} onChange={(background) => setStyle({ background }, 'background')} error={err('style.background')} />
          )}
        </section>
      </div>
    </>
  )
}
