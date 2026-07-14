from typing import Optional, List
from pydantic import BaseModel

class PortfolioHolding(BaseModel):
    ticker: str
    quote_type: Optional[str] = "EQUITY"
    allocation_pct: Optional[float] = None
    current_price: Optional[float] = None
    intrinsic_value: Optional[float] = None
    margin_of_safety_pct: Optional[float] = None
    f_score: Optional[int] = None
    gain_loss_pct: Optional[float] = None


class PortfolioInsightsBody(BaseModel):
    holdings: List[PortfolioHolding]
    total_value: Optional[float] = None
    total_gain_loss_pct: Optional[float] = None


