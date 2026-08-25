package org.twak.tweed.gen;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Focused, dependency-free validation for the vertex-colour OBJ parser. */
public final class VertexColorObjGenValidation {
	private VertexColorObjGenValidation() {}

	public static void main( String[] args ) throws Exception {
		if ( args.length == 1 ) {
			VertexColorObjGen.ParsedObj actual = VertexColorObjGen.parse( new java.io.File( args[ 0 ] ) );
			System.out.println( "VertexColorObjGen validation passed: " + actual.vertexCount()
					+ " vertices, " + actual.triangleCount() + " triangles" );
			return;
		}
		Path obj = Files.createTempFile( "vertex-colour-", ".obj" );
		Files.write( obj, ( "v 0 0 0 255 0 0\n"
				+ "v 1 0 0 0 255 0\n"
				+ "v 1 1 0 0 0 255\n"
				+ "v 0 1 0 1 1 1\n"
				+ "f 1 2 3 4\n"
				+ "f -4/-1 -3/-1 -2/-1\n" ).getBytes( StandardCharsets.UTF_8 ) );
		VertexColorObjGen.ParsedObj parsed = VertexColorObjGen.parse( obj.toFile() );
		if ( parsed.vertexCount() != 4 || parsed.triangleCount() != 3 )
			throw new AssertionError( "n-gon triangulation or negative indices failed" );
		if ( parsed.colors[ 0 ] != 1f || parsed.colors[ 1 ] != 0f || parsed.colors[ 2 ] != 0f
				|| parsed.colors[ 12 ] != 1f || parsed.colors[ 13 ] != 1f || parsed.colors[ 14 ] != 1f )
			throw new AssertionError( "RGB normalization failed" );
		for ( float value : parsed.normals )
			if ( Float.isNaN( value ) || Float.isInfinite( value ) )
				throw new AssertionError( "normal generation produced a non-finite value" );

		Path missing = Files.createTempFile( "vertex-no-colour-", ".obj" );
		Files.write( missing, "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n".getBytes( StandardCharsets.UTF_8 ) );
		boolean rejected = false;
		try { VertexColorObjGen.parse( missing.toFile() ); }
		catch ( java.io.IOException expected ) { rejected = true; }
		if ( !rejected ) throw new AssertionError( "missing RGB must trigger BlockGen fallback" );
		Files.deleteIfExists( obj );
		Files.deleteIfExists( missing );
		System.out.println( "VertexColorObjGen validation passed" );
	}
}
