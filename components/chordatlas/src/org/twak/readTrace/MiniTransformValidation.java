package org.twak.readTrace;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;

import javax.vecmath.Matrix4d;

import org.apache.commons.io.FileUtils;

/** Verifies that conversion closes every handle before a Windows directory move. */
public final class MiniTransformValidation {

	private MiniTransformValidation() {}

	public static void main( String[] args ) throws Exception {
		Path root = Files.createTempDirectory( "mini-transform-move-" );
		try {
			Path input = root.resolve( "tetra.obj" );
			Files.write( input, (
					"v 0 0 0\n" +
					"v 1 0 0\n" +
					"v 0 1 0\n" +
					"v 0 0 1\n" +
					"f 1 3 2\n" +
					"f 1 2 4\n" +
					"f 1 4 3\n" +
					"f 2 3 4\n" ).getBytes( StandardCharsets.UTF_8 ) );

			Path partial = root.resolve( "minimesh.part" );
			Matrix4d identity = new Matrix4d();
			identity.setIdentity();
			MiniTransform.convertToMini(
					Collections.singletonList( input.toFile() ), partial.toFile(), identity );

			Path active = root.resolve( "minimesh" );
			Files.move( partial, active );
			if ( !Files.isRegularFile( active.resolve( MiniTransform.INDEX ) )
					|| !Files.isRegularFile( active.resolve( "0" ).resolve( MiniTransform.OBJ ) ) )
				throw new AssertionError( "moved MiniMesh is incomplete" );
		} finally {
			FileUtils.deleteDirectory( root.toFile() );
		}
		System.out.println( "MiniTransform Windows move validation passed" );
	}
}
