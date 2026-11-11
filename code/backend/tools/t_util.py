# -*- coding: utf-8 -*-
import ast
import operator
import re


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _fonctions() -> dict:
    import math
    import statistics as st

    def factorielle(n):
        if not float(n).is_integer() or not 0 <= n <= 5000:
            raise ValueError("factorielle : entier entre 0 et 5000")
        return math.factorial(int(n))

    return {
        "sqrt": math.sqrt, "racine": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
        "exp": math.exp, "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2,
        "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
        "atan": math.atan, "degrees": math.degrees, "radians": math.radians,
        "factorial": factorielle, "factorielle": factorielle, "comb": math.comb, "perm": math.perm,
        "gcd": math.gcd, "pgcd": math.gcd, "lcm": math.lcm, "ppcm": math.lcm,
        "floor": math.floor, "ceil": math.ceil, "round": round, "abs": abs,
        "min": min, "max": max, "sum": sum, "len": len,
        "mean": st.mean, "moyenne": st.mean, "median": st.median, "mediane": st.median,
        "stdev": st.stdev, "ecart_type": st.stdev, "pstdev": st.pstdev,
        "variance": st.variance, "pvariance": st.pvariance, "mode": st.mode,
    }


_CONSTANTES = {"pi": 3.141592653589793, "e": 2.718281828459045, "tau": 6.283185307179586}


def _safe_eval(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTES:
        return _CONSTANTES[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        if len(node.elts) > 10000:
            raise ValueError("liste trop longue")
        return [_safe_eval(e) for e in node.elts]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        gauche, droite = _safe_eval(node.left), _safe_eval(node.right)
        # Garde : 2**10**10 ou 9**9**9 bloquaient le processus.
        if isinstance(node.op, ast.Pow) and (abs(droite) > 10000 or (abs(gauche) > 1e6 and abs(droite) > 100)):
            raise ValueError("puissance trop grande")
        return _OPS[type(node.op)](gauche, droite)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords
            and node.func.id in _fonctions()):
        args = [_safe_eval(a) for a in node.args]
        return _fonctions()[node.func.id](*args)
    raise ValueError("Expression non autorisee")


def tool_calculator(expression: str) -> str:
    expr = (expression or "").strip().replace("×", "*").replace("÷", "/").replace("^", "**")
    try:
        result = _safe_eval(ast.parse(expr, mode="eval"))
    except Exception as exc:
        return (f"Erreur de calcul ({exc}). Autorise : + - * / // % **, parentheses, listes [..], "
                "constantes pi e, fonctions " + ", ".join(sorted(_fonctions())) +
                ". Exemple : stdev([3, 7, 9, 12]) ou sqrt(2025).")
    if isinstance(result, float):
        if result.is_integer():
            result = int(result)
        else:
            result = round(result, 10)
    # L'expression est rappelee : l'agent voit QUEL calcul il a fait (il
    # annoncait une « variance » de 13,49 calculee avec stdev, 09/11/2026).
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", expr):
        return str(result)
    return f"{expr} = {result}"


def tool_get_datetime() -> str:
    from datetime import datetime

    return datetime.now().strftime("%A %d %B %Y, %H:%M:%S")
