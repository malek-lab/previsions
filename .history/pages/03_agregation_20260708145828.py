2026-07-08 14:46:49.073 Serialization of dataframe to Arrow table was unsuccessful. Applying automatic fixes for column types to make the dataframe Arrow-compatible.
Traceback (most recent call last):
  File "C:\Users\msaid\Documents\Projet automatisation\venv\Lib\site-packages\streamlit\dataframe_util.py", line 961, in convert_pandas_df_to_arrow_bytes
    table = pa.Table.from_pandas(df)
  File "pyarrow/table.pxi", line 4768, in pyarrow.lib.Table.from_pandas
  File "C:\Users\msaid\Documents\Projet automatisation\venv\Lib\site-packages\pyarrow\pandas_compat.py", line 651, in dataframe_to_arrays
    arrays = [convert_column(c, f)
              ~~~~~~~~~~~~~~^^^^^^
  File "C:\Users\msaid\Documents\Projet automatisation\venv\Lib\site-packages\pyarrow\pandas_compat.py", line 639, in convert_column
    raise e
  File "C:\Users\msaid\Documents\Projet automatisation\venv\Lib\site-packages\pyarrow\pandas_compat.py", line 633, in convert_column
    result = pa.array(col, type=type_, from_pandas=True, safe=safe)
  File "pyarrow/array.pxi", line 390, in pyarrow.lib.array
  File "pyarrow/array.pxi", line 91, in pyarrow.lib._ndarray_to_array
  File "pyarrow/error.pxi", line 92, in pyarrow.lib.check_status
pyarrow.lib.ArrowTypeError: ("Expected bytes, got a 'int' object", 'Conversion failed for column REF_ARTICLE_CLIENT with type object')














