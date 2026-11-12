export interface Me {
  user_id: string
  name: string
  username: string
  is_admin: boolean
  max_accounts: number
}

export interface MarketStatus {
  stocks: number
  last_trade_date: string | null
  updated_at: string | null
}
