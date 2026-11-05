/**
 * Host tools for the svg-rebuild-and-paint skill.
 *
 * Each tool is a thin wrapper over one existing command line - no new
 * pipeline, no bespoke data flow. The point is structural discovery: the
 * capability shows up in the tool list (name + parameters + description),
 * so the model can see and choose it instead of matching trigger words in
 * prose.
 */
import { spawnSync } from 'node:child_process'
import { defineTool } from '@deepseek-ai/dsh-tools'
import fs from 'node:fs'
import path from 'node:path'

export const name = 'svg-paint-tools'

const DEFAULT_SKILL = process.env.DSH_SKILLS_DIR
  ? path.join(process.env.DSH_SKILLS_DIR, 'svg-rebuild-and-paint')
  : path.join(process.env.DSH_HOME || 'D:/deepseek-harness-dsh-v0.2.2rc1/.dsh-home', 'skills', 'svg-rebuild-and-paint')

function resolveSkill(config) {
  const dir = (config && typeof config.skillDir === 'string' && config.skillDir.length > 0) ? config.skillDir : DEFAULT_SKILL
  const scripts = path.join(dir, 'scripts')
  if (!fs.existsSync(scripts)) throw new Error('svg-rebuild-and-paint scripts not found at ' + scripts)
  return scripts
}

function run(scripts, script, args) {
  const started = Date.now()
  const res = spawnSync('python', [path.join(scripts, script)].concat(args), {
    cwd: scripts,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }),
  })
  const seconds = (Date.now() - started) / 1000
  if (res.error) throw new Error(script + ': ' + res.error.message)
  if (res.status !== 0) {
    const tail = ((res.stderr || '') + (res.stdout || '')).trim().split(/\r?\n/).slice(-6).join('\n')
    throw new Error(script + ' exited ' + String(res.status) + ': ' + tail)
  }
  return { stdout: res.stdout || '', stderr: res.stderr || '', seconds }
}

function abs(p) { return path.isAbsolute(p) ? p : path.resolve(process.cwd(), p) }

function statFile(p) {
  const s = fs.statSync(abs(p))
  return { path: abs(p), bytes: s.size }
}

export function apply(ctx, config) {
  const tools = ctx.get('tools')
  if (tools === undefined) return
  let scripts
  try {
    scripts = resolveSkill(config)
  } catch (error) {
    console.error('[svg-paint-tools] not mounted: ' + (error && error.message ? error.message : String(error)))
    return
  }

  tools.register(define({
    name: 'art_vectorize',
    description: '把一张位图（PNG/JPEG）重建成真正的矢量 SVG：逐色掩膜 + 曲线重建，输出纯 <path> 平涂几何（无 <image>、无 base64、无渐变），任意放大无台阶。返回 SVG 路径数、<path> 条数与文件大小。想量误差时再用 metric.py 比原图与渲染图。',
    parameters: {
      image: { type: 'string', description: '输入位图路径（绝对路径或相对当前工作目录）' },
      out: { type: 'string', description: '输出 SVG 路径' },
      k: { type: 'number', description: '可选，调色板色数上限（例如 128/256/384；不给用脚本默认）' },
      ss: { type: 'number', description: '可选，超采样倍率（2 或 3；不给用脚本默认）' },
    },
    async execute(args) {
      const argv = [abs(args.image), abs(args.out)]
      if (typeof args.k === 'number') argv.push('--k', String(Math.round(args.k)))
      if (typeof args.ss === 'number') argv.push('--ss', String(Math.round(args.ss)))
      const r = run(scripts, 'vectorize.py', argv)
      const svg = fs.readFileSync(abs(args.out), 'utf8')
      const paths = (svg.match(/<path/g) || []).length
      const info = statFile(args.out)
      return [
        'SVG: ' + info.path,
        'path elements: ' + String(paths),
        'size: ' + (info.bytes / 1048576).toFixed(2) + ' MB',
        'seconds: ' + r.seconds.toFixed(1),
      ].join('\n')
    },
  }))

  tools.register(define({
    name: 'art_paint',
    description: '把一个绘画计划（plan.json / plan.npz：canvas + layers + strokes）确定性地渲染成一张图。同一个计划每次渲染逐像素一致；可用 ops 文件把改动叠加上去。这就是厚涂绘画引擎的渲染入口。',
    parameters: {
      plan: { type: 'string', description: '计划文件路径（plan.json 或 plan.npz）' },
      out: { type: 'string', description: '输出 PNG 路径' },
      ops: { type: 'string', description: '可选，ops JSON 路径（按顺序作用在计划上）' },
      gpu: { type: 'boolean', description: '可选，用 DirectCompute GPU 后端渲染' },
    },
    async execute(args) {
      const argv = [abs(args.plan), '--out', abs(args.out)]
      if (typeof args.ops === 'string' && args.ops.length > 0) argv.push('--ops', abs(args.ops))
      if (args.gpu === true) argv.push('--gpu')
      const r = run(scripts, 'render_plan.py', argv)
      const info = statFile(args.out)
      return ['PNG: ' + info.path, 'size: ' + (info.bytes / 1024).toFixed(0) + ' KB', 'seconds: ' + r.seconds.toFixed(2)].join('\n')
    },
  }))

  tools.register(define({
    name: 'art_edit_brief',
    description: '把一句自然语言改图指令编译成一次「可执行的编辑初审」：产出计划摘要 + 当前画面缩略图（零 API key，走当前会话的多模态模型）。先调这个，然后自己读缩略图、按 brief 里的 prompt 写出 ops JSON，再调 art_apply_ops 应用。适用于按指令改画：改色、调光、细化、局部重画。',
    parameters: {
      plan: { type: 'string', description: '计划文件路径' },
      instruction: { type: 'string', description: '一句改图指令，例如「背景压深成墨绿夜色」「把主体改成冷调」' },
      out: { type: 'string', description: '输出 brief JSON 路径（同目录会写出缩略图）' },
    },
    async execute(args) {
      const r = run(scripts, 'compile_ops.py', ['brief', abs(args.plan), args.instruction, '--thumb', '--out', abs(args.out)])
      const brief = JSON.parse(fs.readFileSync(abs(args.out), 'utf8'))
      const thumb = brief.thumb && brief.thumb.path ? String(brief.thumb.path) : (typeof brief.thumb === 'string' ? brief.thumb : '')
      const out = ['brief: ' + abs(args.out)]
      if (thumb.length > 0) out.push('thumb: ' + thumb, '下一步：读这张缩略图 → 按下面的 prompt 写 ops JSON → 调 art_apply_ops')
      out.push('', String(brief.prompt || '').slice(0, 4000))
      out.push('', r.stdout.trim().slice(0, 800))
      return out.join('\n')
    },
  }))

  tools.register(define({
    name: 'art_apply_ops',
    description: '把 ops JSON 应用到绘画计划并渲染出图，返回改动像素数（与不应用 ops 的渲染逐像素比较）。ops 由 art_edit_brief 之后自己编写，或用 compile_ops.py compile 生成。',
    parameters: {
      plan: { type: 'string', description: '计划文件路径' },
      ops: { type: 'string', description: 'ops JSON 路径' },
      out: { type: 'string', description: '输出 PNG 路径' },
      gpu: { type: 'boolean', description: '可选，用 GPU 后端' },
    },
    async execute(args) {
      const argv = ['edit', abs(args.plan), '--ops', abs(args.ops), '--out', abs(args.out), '--fast']
      if (args.gpu === true) argv.push('--gpu')
      const r = run(scripts, 'paint_cli.py', argv)
      const info = statFile(args.out)
      const tail = (r.stdout || '').trim().split(/\r?\n/).slice(-4).join('\n')
      return ['PNG: ' + info.path, 'size: ' + (info.bytes / 1024).toFixed(0) + ' KB', 'seconds: ' + r.seconds.toFixed(2), tail].filter((x) => x.length > 0).join('\n')
    },
  }))
}

const OUT = { schema: { type: 'string' }, render(_args, value) { return [{ type: 'text', text: String(value) }] } }
function define(spec) { return defineTool(Object.assign({ output: OUT }, spec)) }
