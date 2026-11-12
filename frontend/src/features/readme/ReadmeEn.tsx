import Example from './Example'
import { readmeClass } from './readmeClass'

export default function ReadmeEn({ marketStatus, onExample }: { marketStatus: string; onExample(text: string): void }) {
  return (
    <div className={readmeClass}>
      <h1>How to use smallie</h1>
      <p>smallie is a chat assistant with four specialists. You just ask. The <strong>Assistant</strong> answers general
        questions itself (explanations, maths, writing, a quick web search for recent facts), passes a question to the right
        specialist, or asks several of them and combines their answers. The badge above each reply shows who answered.
        If none of them can do what you ask, it tells you so.</p>
      <ul>
        <li><strong>Documents agent</strong> answers from the PDF and Word files you upload, and cites the file and page.</li>
        <li><strong>Database agent</strong> answers from a database of 200 popular stocks
          by writing and running read-only SQL. Expand <em>Used tool</em> above an answer to see the query it ran.</li>
        <li><strong>Research agent</strong> looks a topic up on the web, Wikipedia and arXiv (plus the stock database and
          your files when they help); the Assistant then writes a short summary or a structured report with sources.</li>
        <li><strong>Chart agent</strong> draws line, bar, area, pie and scatter charts of the stock data, or of numbers
          from another answer, and sums up what they show.</li>
      </ul>
      <p>Write in English or Vietnamese; answers come back in the language you used.</p>
      
      <h2>Ask about your documents</h2>
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
      
      <h2>Ask about stocks</h2>
      <p>The stock database covers 200 widely held stocks (Apple, Microsoft, NVIDIA, Tesla, JPMorgan, Coca-Cola…) with:</p>
      <ul>
        <li>company profile and key stats: sector, industry, market cap, P/E, EPS, dividend yield, beta, 52-week range, revenue, profit margin;</li>
        <li>daily prices for about 5 years: open, high, low, close, adjusted close and volume.</li>
      </ul>
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
      
      <h2>Research a topic</h2>
      <p>Say "research", "look up online", "latest news about…" or ask for a report. The agent searches several sources
        (you can follow along under <em>Used tool</em>), then answers with a summary, or with a report, comparison or
        company brief when you ask for one.</p>
      <div className="flex flex-wrap gap-2">
        <Example text={"Research the latest news about NVIDIA and compare it with its stock performance."} onExample={onExample} />
        <Example text={"Write a short report on retrieval-augmented generation, with recent papers."} onExample={onExample} />
      </div>
      <p className="text-sm text-muted-foreground">Research takes more AI calls than other questions, so it is slower and uses more of the free quota.</p>
      
      <h2>Draw a chart</h2>
      <p>Say "chart", "plot" or "graph". After a database answer, "plot that" draws it without querying again. Hover a
        chart for exact values; under it, <em>Show data</em> lists the numbers and <em>Download PNG</em> saves the picture.</p>
      <div className="flex flex-wrap gap-2">
        <Example text={"Chart AAPL vs MSFT closing prices over the last year."} onExample={onExample} />
        <Example text={"Plot the market caps of the 10 largest tech companies."} onExample={onExample} />
      </div>
      
      <h2>Chats and memory</h2>
      <ul>
        <li><strong>+ New chat</strong> starts a fresh conversation. Earlier chats are in the sidebar; one conversation can mix document and stock questions.</li>
        <li>Follow-ups work: "and over 3 years?", "show that as a table".</li>
        <li>smallie remembers durable facts you tell it (your name, role, preferred answer style) and can look back at
          your earlier chats, so you don't have to repeat yourself. Deleting a chat removes it from that history.</li>
        <li>Everyone has their own account: your chats, files and memories are visible only to you.</li>
      </ul>
      
      <h2>Your model server</h2>
      <table>
        <thead><tr><th>Provider</th><th>Key page</th><th>API endpoint</th><th>Model</th></tr></thead>
        <tbody>
          <tr><td>DeepSeek (cheap, takes local cards)</td><td>platform.deepseek.com</td><td>https://api.deepseek.com/v1</td><td>deepseek-chat</td></tr>
          <tr><td>OpenAI</td><td>platform.openai.com</td><td>https://api.openai.com/v1</td><td>gpt-4o-mini</td></tr>
          <tr><td>OpenRouter</td><td>openrouter.ai</td><td>https://openrouter.ai/api/v1</td><td>your choice</td></tr>
          <tr><td>Alibaba DashScope</td><td>dashscope.aliyun.com</td><td>https://dashscope-intl.aliyuncs.com/compatible-mode/v1</td><td>qwen-plus</td></tr>
        </tbody>
      </table>
      <p>Any OpenAI-compatible service works.</p>
      <p>Open <strong>Settings</strong> in the left sidebar and enter your API key, the API endpoint that matches the key's provider, and a model. Then press <strong>Save</strong>, which checks the connection and saves.</p>
      <p className="text-sm text-muted-foreground">Using one provider's key with another provider's endpoint is the most common mistake; the app says so directly.</p>
      <p className="text-sm text-muted-foreground">Your key is stored encrypted on the server and sent only to the provider you chose. Your messages, parts of the answers and the documents you upload are also turned into search vectors (embeddings) through OpenRouter, on the app's account.</p>
      
      
      <h2>Models and profiles</h2>
      <p>In team mode the assistant rates every task <strong>easy</strong>, <strong>medium</strong> or <strong>hard</strong>.
        Your <strong>profile</strong> in Settings then picks which of your models does it: the <em>small</em> model for light work,
        the <em>standard</em> model, or the <em>strong</em> model for the hardest work. A 1v1 chat is rated the same way, once per message.</p>
      <ul>
        <li><strong>Economy</strong> (spend less): easy and medium tasks go to the small model, hard tasks to the standard model.</li>
        <li><strong>Balanced</strong> (recommended): easy tasks go to the small model, medium to the standard model, hard to the strong model.</li>
        <li><strong>Advanced</strong> (best answers): easy tasks go to the standard model, medium and hard to the strong model.</li>
      </ul>
      <p>The standard model is required. The small and strong models are optional: when one is empty, its tasks use the standard model.
        If a small or strong model fails (for example the name is wrong, or the provider rate-limits it), the same step is repeated once on the standard model.
        The <em>Activity</em> button above a reply shows which model handled each task and why.</p>
      
      <h2>Tips</h2>
      <ul>
        <li>Name what you want: a company or ticker, a time range, the number of results.</li>
        <li>If the wrong agent answers, say it outright, e.g. "according to my documents…" or "from the stock database…".</li>
        <li>Each question takes a few AI calls. If you see a rate-limit message, wait a minute and try again.</li>
      </ul>
    </div>
  )
}
