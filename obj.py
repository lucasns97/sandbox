# Experimento de comparação do obj.pop com (try/except, None) e (get+pop, None)

# CONTEXT
print('--------- CONTEXT ---------')
print('---------------------------')

# POP
print('1. Pop')
obj = {'b': 1}
print('> Create obj:', obj)
print('> Running "obj.pop("a")" with "try/except", return None')
print("""> Example:
\ttry:
\t    a = obj.pop('a')
\texcept KeyError:
\t    a = None
""")

# pop -> this uses try/except block and assign None to the created variable
try:
    a = obj.pop('a')
except KeyError:
    a = None

print('> After obj:', obj)
print('> Result:', a)
print()

# GET+POP
print('1. Get+Pop')
obj = {'b': 1}
print('> Create obj:', obj)
print('> Running "obj.pop("a") with obj.get("a")", return None')
print("""> Example:
\ttry:
\t    a = obj.pop('a') if obj.get('a') else None
""")

# get pop -> this uses conditional and .get to assign None to the created variable
a = obj.pop('a') if obj.get('a') else None

print('> After obj:', obj)
print('> Result:', a)
print()

# TESTS
import timeit
from time import time
from functools import partial


def pop_func(obj):
    """
    Pop function
    """

    # pop
    try:
        a = obj.pop('a')
    except KeyError:
        a = None

    return a

def get_pop_func(obj):
    """
    Get+Pop function
    """

    # get pop
    a = obj.pop('a') if obj.get('a') else None

    return a

def test_run(func, obj=None, number=1000, verbose=0, *args, **kwargs):
    """
    Test run function
    
    :param func: function to run
    :param obj: object to make nomber copies to run
    :param number: number of runs
    :param verbose: verbose level
    :param args: args to pass to function
    :param kwargs: kwargs to pass to function
    :return: time to run
    """

    # Copy obj
    if obj is None:
        objs = [{}]*number
    else:
        objs = [obj.copy() for _ in range(number)]

    # Run
    start_time = time()
    for it_obj in objs:
        res = func(it_obj, *args, **kwargs)
    end_time = time()

    # Print result
    if verbose == 1:
        print(number, 'it', func.__name__, end_time - start_time, 'sec')
    elif verbose >= 2:
        print(f'> Func: {func.__name__}', '| obj:', obj, '| args:', args, '| kwargs:', kwargs, '| res:', res,)
        print(f'> Iterations: {number}')
        print(f'> Time: {round(end_time - start_time, 5)} sec')
        print(f'> It/sec: {round(number/(end_time - start_time), 5)}\n')

    return end_time - start_time


# CONSTRAINS
VERBOSE = 2
ITERERATIONS = 10_000_000


# MAIN
if __name__ == '__main__':

    if VERBOSE >= 2:
        print('--------- RUNNING ---------')
        print('---------------------------')
        print(f'- Iterations: {ITERERATIONS}')
        print('---------------------------\n')


    # Pop (try/except, None)
    print('Pop (try/except, None)')
    obj = {'b': 123}
    test_run(pop_func, obj=obj, number=ITERERATIONS, verbose=VERBOSE)

    print('Get+Pop (get, None)')
    obj = {'b': 123}
    test_run(get_pop_func, obj=obj, number=ITERERATIONS, verbose=VERBOSE)

    print('Pop (try/except, 123)')
    obj = {'a': 123}
    test_run(pop_func, obj=obj, number=ITERERATIONS, verbose=VERBOSE)

    print('Get+Pop (get, 123)')
    obj = {'a': 123}
    test_run(get_pop_func, obj=obj, number=ITERERATIONS, verbose=VERBOSE)

# OUTPUT
"""
--------- CONTEXT ---------
---------------------------
1. Pop
- Create obj: {'b': 1}
- Running "obj.pop("a")" with "try/except", return None
- After obj: {'b': 1}
- Result: None

1. Get+Pop
- Create obj: {'b': 1}
- Running "obj.pop("a") with obj.get("a")", return None
- After obj: {'b': 1}
- Result: None

--------- RUNNING ---------
---------------------------
- Iterations: 10000000
---------------------------

Pop (try/except, None)
Func: pop_func | obj: {'b': 123} | args: () | kwargs: {} | res: None
Iterations: 10000000
Time 3.83992 sec
It/sec: 2604222.8318

Get+Pop (get, None)
Func: get_pop_func | obj: {'b': 123} | args: () | kwargs: {} | res: None
Iterations: 10000000
Time 2.07483 sec
It/sec: 4819677.3831

Pop (try/except, 123)
Func: pop_func | obj: {'a': 123} | args: () | kwargs: {} | res: 123
Iterations: 10000000
Time 2.21639 sec
It/sec: 4511842.0277

Get+Pop (get, 123)
Func: get_pop_func | obj: {'a': 123} | args: () | kwargs: {} | res: 123
Iterations: 10000000
Time 2.61618 sec
It/sec: 3822366.00065
"""

# RESTULS
"""
When obj doesn't contains the key to be popped:

    Pop (try/except, None) - It/sec: ~2419331
    Get+Pop (get, None) - It/sec: ~4761677 (1.96x)

When obj contains the key to be popped:

    Pop (try/except, 123) - It/sec: ~4180299
    Get+Pop (get, 123) - It/sec: ~3822366 (0.86x)
"""
