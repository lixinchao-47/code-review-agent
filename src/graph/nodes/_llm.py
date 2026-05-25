"""共享 LLM 实例，供所有节点模块导入"""

from config import DEEPSEEK_API_KEY, LLM_MODEL
from langchain_deepseek import ChatDeepSeek

llm = ChatDeepSeek(
    api_key=DEEPSEEK_API_KEY,
    model=LLM_MODEL,
    temperature=0.1,
)
