import Example from './Example'
import { readmeClass } from './readmeClass'
import usageButton from './usage-button.png'

export default function ReadmeEn({ marketStatus, onExample }: { marketStatus: string; onExample(text: string): void }) {
  return (
    <div className={readmeClass}>
      <h1>How to use smallie</h1>
      <p>Open a section below for the details.</p>

      <details>
        <summary>What is smallie?</summary>
        <p>smallie is a chat assistant with four specialists. You just ask. The <strong>Assistant</strong> answers general
          questions itself, passes a question to the right specialist, or asks several of them and combines their answers.
          The badge above each reply shows who answered.</p>
        <ul>
          <li>Write in English or Vietnamese; answers come back in the language you used.</li>
          <li>Everyone has their own account: your chats, files and data are visible only to you.</li>
          <li>Name what you want: a company or ticker, a time range, the number of results.</li>
          <li>If the wrong agent answers, say it outright, e.g. "according to my documents…" or "from the stock database…".</li>
        </ul>
      </details>

      <details>
        <summary>The agents</summary>
        <details>
          <summary>Docs</summary>
          <p>Answers from the PDF and Word files you upload, and cites the file and page.</p>
          <ol>
            <li>Click <strong>Upload PDF / DOCX</strong> and choose one or more files (up to 25 MB each).</li>
            <li>Wait until the file shows <em>ready</em>. Indexing takes a few seconds to a minute.</li>
            <li>Ask a question. Click an example to put it in the message box:</li>
          </ol>
          <div className="flex flex-wrap gap-2">
            <Example text={"Summarize the main points of my documents."} onExample={onExample} />
            <Example text={"What does the contract say about termination?"} onExample={onExample} />
          </div>
          <p className="text-sm text-muted-foreground">Only you can see your files. Remove one with the × next to its name.</p>
        </details>
        <details>
          <summary>Stock database</summary>
          <p>200 widely held stocks (Apple, Microsoft, NVIDIA, Tesla, JPMorgan, Coca-Cola…), with a company profile and key
            stats, and daily prices for about 5 years. The agent answers by writing and running read-only SQL; expand
            <em> Used tool</em> above an answer to see the query it ran.</p>
          <p>{marketStatus}</p>
          <div className="flex flex-wrap gap-2">
            <Example text={"What are the 10 largest companies by market cap?"} onExample={onExample} />
            <Example text={"How did NVDA, AMD and INTC perform over the last 12 months?"} onExample={onExample} />
            <Example text={"Which stocks gained the most on the latest trading day?"} onExample={onExample} />
            <Example text={"Compare the average P/E ratio by sector."} onExample={onExample} />
            <Example text={"Show Apple's closing price at the end of each month this year."} onExample={onExample} />
          </div>
          <p className="text-sm text-muted-foreground">Prices are end-of-day data from Yahoo Finance, not live quotes. They update automatically every weekday
            evening after the US market closes. This is not financial advice.</p>
        </details>
        <details>
          <summary>Research</summary>
          <p>Say "research", "look up online", "latest news about…" or ask for a report. The agent searches the web, Wikipedia
            and arXiv (plus the stock database and your files when they help), then answers with a summary, or with a report,
            comparison or company brief with sources when you ask for one.</p>
          <div className="flex flex-wrap gap-2">
            <Example text={"Research the latest news about NVIDIA and compare it with its stock performance."} onExample={onExample} />
            <Example text={"Write a short report on retrieval-augmented generation, with recent papers."} onExample={onExample} />
          </div>
          <p className="text-sm text-muted-foreground">Research takes more AI calls than other questions, so it is slower and uses more of your quota.</p>
        </details>
        <details>
          <summary>Chart</summary>
          <p>Draws line, bar, area, pie and scatter charts. Say "chart", "plot" or "graph". After a database answer, "plot that"
            draws it without querying again. Hover a chart for exact values; under it, <em>Show data</em> lists the numbers
            and <em>Download PNG</em> saves the picture.</p>
          <div className="flex flex-wrap gap-2">
            <Example text={"Chart AAPL vs MSFT closing prices over the last year."} onExample={onExample} />
            <Example text={"Plot the market caps of the 10 largest tech companies."} onExample={onExample} />
          </div>
        </details>
      </details>

      <details>
        <summary>Chat modes</summary>
        <ul>
          <li><strong>1-to-1</strong> (default): the Assistant picks who answers your message.</li>
          <li><strong>Team mode</strong> (the switch in the sidebar): the Assistant plans tasks for several specialists, shows
            them working as a team, and writes the answer. It costs more AI calls.</li>
          <li><strong>+ New chat</strong> starts a fresh conversation. Earlier chats are in the sidebar. Follow-ups work: "and over 3 years?", "show that as a table".</li>
          <li>The <em>Activity</em> button above a reply shows what happened behind it.</li>
        </ul>
      </details>

      <details>
        <summary>Usage tracking</summary>
        <p>The <strong>Usage</strong> button below the chat box opens the tokens and cost of this chat, for the last reply and
          in total. The box in the sidebar shows your account's total.</p>
        <img src={usageButton} alt="The Usage button below the Send button of the chat box" className="rounded-md border" />
      </details>
    </div>
  )
}
