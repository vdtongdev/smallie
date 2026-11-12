import AuthScreen from '@/features/auth/AuthScreen'
import { useAuth } from '@/features/auth/AuthProvider'
import Header from '@/features/shell/Header'
import Shell from '@/features/shell/Shell'

export default function App() {
  const { status } = useAuth()
  return (
    <div className="min-h-screen bg-background text-foreground">
      {status === 'in' ? (
        <Shell />
      ) : (
        <>
          <Header />
          {status === 'out' && <AuthScreen />}
        </>
      )}
    </div>
  )
}
