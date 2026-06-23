'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Renders assistant content as GitHub-flavored markdown inside a `.md` scope
 * (styles live in globals.css). Handles the full range of tool outputs:
 * headings, bold/italic, ordered/unordered lists, tables, inline + block code,
 * blockquotes, and links. Links open in a new tab; long URLs and code/tables
 * wrap or scroll inside their own box so nothing forces the page wider.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
