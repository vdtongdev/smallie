import { Button } from '@/components/ui/button'

export default function Example({ text, onExample }: { text: string; onExample(text: string): void }) {
  return (
    <Button type="button" variant="outline" className="h-auto whitespace-normal text-left" onClick={() => onExample(text)}>
      {text}
    </Button>
  )
}
