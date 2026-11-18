// Panel derecho: propiedades del elemento seleccionado según su tipo. Sin selección
// muestra las del tema (ThemePanel).

import type { Dispatch } from 'react'
import type { Action } from '../model/editorState.ts'
import { formatClock, formatReading } from '../model/format.ts'
import { CLOCK_FORMAT_INFO, WIDGET_TYPE_INFO } from '../model/theme.ts'
import { LIMITS, screenSize, type Align, type ClockFormat, type Theme, type Widget } from '../model/types.ts'
import { fieldOf, type Issue } from '../model/validate.ts'
import { ColorField, NumberField, Segmented as SegmentedField, SelectField, TextField, Toggle } from './fields.tsx'
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

export function PropertiesPanel({ theme, widget, id, issues, services, dispatch, onDuplicate, onRemove }: Props) {
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

  const info = WIDGET_TYPE_INFO[widget.type]
  const b = widget.box
  const isTextLike = widget.type === 'text' || widget.type === 'clock' || widget.type === 'sensor'
  const sensorInfo = widget.type === 'sensor' ? services.sensors.find((s) => s.id === widget.sensor) : undefined
  const sensorIsText = sensorInfo?.kind === 'text'

  return (
    <>
      <div className="props-title">
        <WidgetIcon type={widget.type} />
        <div>
          <h2>{info.label}</h2>
          <p>{info.help}</p>
        </div>
      </div>
      <div className="props-actions">
        <button type="button" className="btn small" onClick={onDuplicate} title="Hacer una copia de este elemento (Ctrl+D)">
          <IconCopy /> Duplicar
        </button>
        <button type="button" className="btn small danger" onClick={onRemove} title="Quitar este elemento (Supr)">
          <IconTrash /> Borrar
        </button>
      </div>

      <div className="panel-body">
        {general.map((g) => (
          <p key={g.message} className="err" role="alert">
            {g.message}
          </p>
        ))}

        <section className="section">
          <h3>Posición y tamaño</h3>
          <p className="section-desc">En píxeles de la pantalla ({screen.width} × {screen.height}). También puedes arrastrarlo en el lienzo.</p>
          <div className="row">
            <NumberField label="Desde la izquierda" affix="X" value={b.x} min={0} max={screen.width - b.width} onChange={(x) => setBox({ x }, 'box.x')} error={err('box.x')} />
            <NumberField label="Desde arriba" affix="Y" value={b.y} min={0} max={screen.height - b.height} onChange={(y) => setBox({ y }, 'box.y')} error={err('box.y')} />
            <NumberField label="Ancho" affix="An" value={b.width} min={1} max={screen.width - b.x} onChange={(width) => setBox({ width: Math.min(width, screen.width - b.x) }, 'box.width')} error={err('box.width')} />
            <NumberField label="Alto" affix="Al" value={b.height} min={1} max={screen.height - b.y} onChange={(height) => setBox({ height: Math.min(height, screen.height - b.y) }, 'box.height')} error={err('box.height')} />
          </div>
          {/* Errores del recuadro entero («widgets.N.box», p. ej. «se sale de la pantalla»). */}
          {err('box') && (
            <p className="err" role="alert">
              {err('box')}
            </p>
          )}
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" className="btn small" onClick={() => setBox({ x: Math.round((screen.width - b.width) / 2) }, 'center')} title="Centrar de izquierda a derecha">
              <IconCenterH /> Centrar
            </button>
            <button type="button" className="btn small" onClick={() => setBox({ y: Math.round((screen.height - b.height) / 2) }, 'center')} title="Centrar de arriba abajo">
              <IconCenterV /> Centrar
            </button>
          </div>
        </section>

        {widget.type === 'text' && (
          <section className="section">
            <h3>Contenido</h3>
            <TextField label="Texto" multiline value={widget.text} maxLength={LIMITS.textMax} onChange={(text) => update({ ...widget, text }, 'text')} hint="Lo que se verá escrito en la pantalla." error={err('text')} />
          </section>
        )}

        {widget.type === 'clock' && (
          <section className="section">
            <h3>Contenido</h3>
            <SelectField
              label="Qué mostrar"
              value={widget.format}
              options={CLOCK_FORMATS.map((f) => ({ value: f, label: `${CLOCK_FORMAT_INFO[f]} — ${formatClock(f, new Date())}` }))}
              onChange={(format) => update({ ...widget, format }, 'format')}
              hint="La hora y la fecha se escriben en español, con el reloj de tu PC."
            />
          </section>
        )}

        {widget.type === 'sensor' && (
          <section className="section">
            <h3>Dato</h3>
            <SensorPicker
              label="Sensor"
              hint="El dato del PC que se enseña. A la derecha ves cuánto vale ahora."
              value={widget.sensor}
              sensors={services.sensors}
              kinds={['number', 'text']}
              onChange={(sensor) => update({ ...widget, sensor }, 'sensor')}
              error={err('sensor')}
            />
            <p className="hint" style={{ marginTop: -4, marginBottom: 10 }}>
              Se verá así: <strong className="live">{formatReading(widget, sensorInfo)}</strong>
            </p>
            {!sensorIsText && (
              <div className="row">
                <NumberField label="Decimales" value={widget.decimals} min={LIMITS.decimalsMin} max={LIMITS.decimalsMax} onChange={(decimals) => update({ ...widget, decimals }, 'decimals')} hint="De 0 a 3." error={err('decimals')} />
                <div className="field">
                  <span className="label">Unidad</span>
                  <Toggle label={sensorInfo?.unit ? `Mostrar «${sensorInfo.unit}»` : 'Mostrar'} checked={widget.show_unit} onChange={(show_unit) => update({ ...widget, show_unit }, 'show_unit')} />
                </div>
              </div>
            )}
            {sensorIsText && (
              <NumberField label="Largo máximo" value={widget.max_chars} min={LIMITS.maxCharsMin} max={LIMITS.maxCharsMax} onChange={(max_chars) => update({ ...widget, max_chars }, 'max_chars')} hint="Si el texto es más largo, se corta con «…»." error={err('max_chars')} />
            )}
            <div className="row">
              <TextField label="Antes" value={widget.prefix} maxLength={LIMITS.shortTextMax} onChange={(prefix) => update({ ...widget, prefix }, 'prefix')} placeholder="CPU " error={err('prefix')} />
              <TextField label="Después" value={widget.suffix} maxLength={LIMITS.shortTextMax} onChange={(suffix) => update({ ...widget, suffix }, 'suffix')} placeholder="" error={err('suffix')} />
            </div>
            <p className="hint" style={{ marginTop: -4, marginBottom: 10 }}>Texto que se añade delante o detrás del valor (deja un espacio si quieres separarlo).</p>
            <NumberField label="Actualizar cada (segundos)" integer={false} step={0.5} value={widget.interval} min={LIMITS.intervalMin} max={LIMITS.intervalMax} onChange={(interval) => update({ ...widget, interval }, 'interval')} hint="Menos segundos = más al día, pero algo más de trabajo para el PC." error={err('interval')} />
          </section>
        )}

        {widget.type === 'bar' && (
          <section className="section">
            <h3>Dato y escala</h3>
            <SensorPicker
              label="Sensor"
              hint="La barra se llena según este dato. Solo salen los que son números."
              value={widget.sensor}
              sensors={services.sensors}
              kinds={['number']}
              onChange={(sensor) => update({ ...widget, sensor }, 'sensor')}
              error={err('sensor')}
            />
            <div className="row">
              <NumberField label="Vacía en" integer={false} value={widget.min} onChange={(min) => update({ ...widget, min }, 'min')} error={err('min')} hint="Valor con la barra vacía." />
              <NumberField label="Llena en" integer={false} value={widget.max} onChange={(max) => update({ ...widget, max }, 'max')} error={err('max')} hint="Valor con la barra llena." />
            </div>
            <SegmentedField
              label="Dirección"
              value={widget.direction}
              options={[
                { value: 'horizontal', label: 'Horizontal', help: 'Se llena de izquierda a derecha' },
                { value: 'vertical', label: 'Vertical', help: 'Se llena de abajo arriba' },
              ]}
              onChange={(direction) => update({ ...widget, direction }, 'direction')}
            />
            <div className="row">
              <ColorField label="Relleno" value={widget.fill} onChange={(fill) => update({ ...widget, fill }, 'fill')} error={err('fill')} />
              <ColorField label="Fondo de la barra" value={widget.track} onChange={(track) => update({ ...widget, track }, 'track')} error={err('track')} />
            </div>
            <NumberField label="Actualizar cada (segundos)" integer={false} step={0.5} value={widget.interval} min={LIMITS.intervalMin} max={LIMITS.intervalMax} onChange={(interval) => update({ ...widget, interval }, 'interval')} error={err('interval')} />
          </section>
        )}

        {widget.type === 'image' && (
          <section className="section">
            <h3>Imagen</h3>
            <ImagePicker
              label="Imagen"
              hint="Se ajusta al recuadro sin deformarse."
              value={widget.file}
              services={services}
              onChange={(file) => file && update({ ...widget, file }, 'file')}
              error={err('file')}
            />
          </section>
        )}

        {isTextLike && (
          <section className="section">
            <h3>Letra</h3>
            <div className="row">
              <NumberField label="Tamaño" value={widget.style.size} min={LIMITS.sizeMin} max={LIMITS.sizeMax} onChange={(size) => setStyle({ size }, 'size')} hint={`De ${LIMITS.sizeMin} a ${LIMITS.sizeMax}.`} error={err('style.size')} />
              <div className="field">
                <span className="label">Grosor</span>
                <Toggle label="Negrita" checked={widget.style.bold} onChange={(bold) => setStyle({ bold }, 'bold')} />
              </div>
            </div>
            <ColorField label="Color" value={widget.style.color} onChange={(color) => setStyle({ color }, 'color')} error={err('style.color')} />
            <SegmentedField<Align>
              label="Alineación"
              value={widget.style.align}
              options={[
                { value: 'left', label: 'Izquierda', icon: <IconAlignLeft /> },
                { value: 'center', label: 'Centro', icon: <IconAlignCenter /> },
                { value: 'right', label: 'Derecha', icon: <IconAlignRight /> },
              ]}
              onChange={(align) => setStyle({ align }, 'align')}
              hint="Dentro de su recuadro. En vertical siempre va centrado."
            />
            <FontPicker value={widget.style.font} services={services} onChange={(font) => setStyle({ font }, 'font')} error={err('style.font')} />
          </section>
        )}

        <section className="section">
          <h3>Fondo del recuadro</h3>
          <Toggle
            label="Pintar un fondo propio"
            checked={widget.style.background !== null}
            onChange={(on) => setStyle({ background: on ? '#1d2027' : null }, 'background')}
            hint="Si está apagado, se ve el fondo del tema."
          />
          {widget.style.background !== null && (
            <ColorField label="Color del fondo" value={widget.style.background} onChange={(background) => setStyle({ background }, 'background')} error={err('style.background')} />
          )}
        </section>
      </div>
    </>
  )
}
