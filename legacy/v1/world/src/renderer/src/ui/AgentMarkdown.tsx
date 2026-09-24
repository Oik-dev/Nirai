import { Fragment } from 'react'
import type { ReactNode } from 'react'

export interface AgentFileReference {
  readonly path: string
  readonly line: number | null
}

export function safeHttpUrl(raw: string): string | null {
  try {
    const url = new URL(raw)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

export function parseAgentFileReference(raw: string): AgentFileReference | null {
  const cleaned = raw.trim()
  if (!cleaned || safeHttpUrl(cleaned)) return null
  const lineMatch = cleaned.match(/^(.*\.[A-Za-z0-9_-]+):(\d+)(?::\d+)?$/)
  const path = lineMatch ? lineMatch[1] : cleaned
  const line = lineMatch ? Number(lineMatch[2]) : null
  const windowsAbsolute = /^[A-Za-z]:[\\/]/.test(path)
  const relativeFile = /[\\/]/.test(path) && /\.[A-Za-z0-9_-]+$/.test(path)
  if (!windowsAbsolute && !relativeFile) return null
  return { path, line }
}

function inlineMarkdown(text: string, agentSessionId?: string): ReactNode[] {
  const pieces = text.split(/(`[^`]+`|\[[^\]]+\]\([^)]+\)|\*\*[^*\n]+\*\*|~~[^~\n]+~~|\*[^*\n]+\*|https?:\/\/[^\s<>()]+|[A-Za-z]:[\\/][^\s<>]+|(?:\.{0,2}[\\/])?[A-Za-z0-9_.-]+[\\/][A-Za-z0-9_./\\-]+\.[A-Za-z0-9_-]+(?::\d+(?::\d+)?)?)/g)
  return pieces.map((piece, index) => {
    const markdownLink = piece.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (markdownLink) {
      const safeUrl = safeHttpUrl(markdownLink[2])
      return safeUrl ? (
        <button
          key={`${piece}-${index}`}
          type="button"
          className="agent-inline-link"
          onClick={() => { void window.nirai.external.open(safeUrl) }}
        >
          {markdownLink[1]}
        </button>
      ) : <Fragment key={`${piece}-${index}`}>{markdownLink[1]}</Fragment>
    }
    if (piece.startsWith('`') && piece.endsWith('`') && piece.length > 2) {
      const code = piece.slice(1, -1)
      const reference = parseAgentFileReference(code)
      return reference && agentSessionId ? (
        <button
          key={`${piece}-${index}`}
          type="button"
          className="agent-file-link"
          onClick={() => { void window.nirai.agent.openFile(reference.path, agentSessionId) }}
        >
          <code>{code}</code>
        </button>
      ) : <code key={`${piece}-${index}`}>{code}</code>
    }
    if (piece.startsWith('**') && piece.endsWith('**') && piece.length > 4) {
      return <strong key={`${piece}-${index}`}>{inlineMarkdown(piece.slice(2, -2), agentSessionId)}</strong>
    }
    if (piece.startsWith('~~') && piece.endsWith('~~') && piece.length > 4) {
      return <del key={`${piece}-${index}`}>{inlineMarkdown(piece.slice(2, -2), agentSessionId)}</del>
    }
    if (piece.startsWith('*') && piece.endsWith('*') && piece.length > 2) {
      return <em key={`${piece}-${index}`}>{inlineMarkdown(piece.slice(1, -1), agentSessionId)}</em>
    }
    const safeUrl = /^https?:\/\//.test(piece) ? safeHttpUrl(piece) : null
    if (safeUrl) {
      return (
        <button
          key={`${piece}-${index}`}
          type="button"
          className="agent-inline-link"
          onClick={() => { void window.nirai.external.open(safeUrl) }}
        >
          {piece}
        </button>
      )
    }
    const reference = parseAgentFileReference(piece)
    if (reference && agentSessionId) {
      return (
        <button
          key={`${piece}-${index}`}
          type="button"
          className="agent-file-link"
          onClick={() => { void window.nirai.agent.openFile(reference.path, agentSessionId) }}
        >
          {piece}
        </button>
      )
    }
    return <Fragment key={`${piece}-${index}`}>{piece}</Fragment>
  })
}

function tableCells(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

export function MarkdownContent({
  text,
  agentSessionId,
  className
}: {
  readonly text: string
  readonly agentSessionId?: string
  readonly className?: string
}): JSX.Element {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const nodes: ReactNode[] = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    const fence = line.match(/^```\s*([^`]*)$/)
    if (fence) {
      const codeLanguage = fence[1].trim()
      const codeLines: string[] = []
      index += 1
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index])
        index += 1
      }
      if (index < lines.length) index += 1
      nodes.push(
        <pre className="markdown-code-block agent-code-block" key={`code-${nodes.length}`}>
          {codeLanguage && <small>{codeLanguage}</small>}
          <code>{codeLines.join('\n')}</code>
        </pre>
      )
      continue
    }

    if (
      /^\s*\|.*\|\s*$/.test(line)
      && index + 1 < lines.length
      && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])
    ) {
      const headers = tableCells(line)
      index += 2
      const rows: string[][] = []
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
        rows.push(tableCells(lines[index]))
        index += 1
      }
      nodes.push(
        <table className="markdown-table agent-markdown-table" key={`table-${nodes.length}`}>
          <thead><tr>{headers.map((cell, cellIndex) => <th key={cellIndex}>{inlineMarkdown(cell, agentSessionId)}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => (
            <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{inlineMarkdown(cell, agentSessionId)}</td>)}</tr>
          ))}</tbody>
        </table>
      )
      continue
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/)
    if (heading) {
      const Tag = heading[1].length === 1 ? 'h3' : heading[1].length === 2 ? 'h4' : 'h5'
      nodes.push(<Tag key={`heading-${nodes.length}`}>{inlineMarkdown(heading[2], agentSessionId)}</Tag>)
      index += 1
      continue
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/)
    if (bullet) {
      nodes.push(<p className="markdown-bullet agent-markdown-bullet" key={`bullet-${nodes.length}`}>• {inlineMarkdown(bullet[1], agentSessionId)}</p>)
      index += 1
      continue
    }
    const numbered = line.match(/^\s*(\d+)\.\s+(.+)$/)
    if (numbered) {
      nodes.push(<p className="markdown-bullet agent-markdown-bullet" key={`number-${nodes.length}`}>{numbered[1]}. {inlineMarkdown(numbered[2], agentSessionId)}</p>)
      index += 1
      continue
    }
    const quote = line.match(/^>\s?(.*)$/)
    if (quote) {
      nodes.push(<blockquote key={`quote-${nodes.length}`}>{inlineMarkdown(quote[1], agentSessionId)}</blockquote>)
      index += 1
      continue
    }
    if (!line.trim()) {
      nodes.push(<span className="markdown-spacer agent-markdown-spacer" key={`space-${nodes.length}`} />)
      index += 1
      continue
    }
    // Raw HTML is deliberately never parsed; React renders it as inert text.
    nodes.push(<p key={`line-${nodes.length}`}>{inlineMarkdown(line, agentSessionId)}</p>)
    index += 1
  }

  return <div className={`markdown-content${className ? ` ${className}` : ''}`}>{nodes}</div>
}

export function AgentMarkdown({ text, agentSessionId }: { readonly text: string; readonly agentSessionId: string }): JSX.Element {
  return <MarkdownContent text={text} agentSessionId={agentSessionId} className="agent-markdown" />
}
