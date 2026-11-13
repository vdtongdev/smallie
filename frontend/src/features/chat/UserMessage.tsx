export default function UserMessage({ text }: { text: string }) {
  return (
    <div className="ml-auto w-fit max-w-[820px] whitespace-pre-wrap rounded-xl bg-muted px-3 py-2">{text}</div>
  )
}
